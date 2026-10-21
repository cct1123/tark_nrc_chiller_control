"""Offline chiller for learning, demos and tests; no hardware connection is opened.

Simulator uses the controller's SerialTransport with SimulatedSerial in place of
pyserial's Serial. Commands therefore pass through the same framing, parsing and
readback checks as hardware. SimulatedSerial stores state and queues reply bytes.
Its thermal response is illustrative, not a model fitted to device measurements.
"""

import math
import time
from dataclasses import replace

from .controller import SerialSettings, SerialTransport


class SimulatedSerial:
    """Implement the serial methods used by SerialTransport, entirely in memory.

    write() applies a command and puts its reply in _buffer; read() consumes that
    buffer. commands retains sent bytes so tests can check exactly what was sent.
    **kwargs accepts the port/baud/timeout options normally passed to pyserial.
    """

    def __init__(self, **kwargs):
        self.is_open = True
        self._buffer = bytearray()
        self.commands: list[bytes] = []
        self.units = "C"
        self.temperature_c = 24.0
        self.target_c = 20.0
        self.loop = "S"
        self.pump = "OFF"
        self.level = "OK"
        self.coolant = "0"
        self.date = "10/14/26"
        self.clock = "12:00:00"
        self._last_update = time.monotonic()

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self._buffer.clear()

    def read(self, size=1):
        """Return up to size queued reply bytes, removing them from the buffer."""
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        if not data:
            time.sleep(0.001)
        return data

    def _display_temperature(self, temperature_c):
        """Convert stored Celsius to the simulated front panel's selected units."""
        if self.units == "F":
            return temperature_c * 1.8 + 32
        return temperature_c

    def write(self, payload):
        """Apply a query/write and queue an ASCII reply, as a serial device would."""
        self.commands.append(payload)
        now = time.monotonic()
        elapsed_s = now - self._last_update
        self._last_update = now
        # Move about 63% toward equilibrium per 12 seconds. No background thread:
        # advance temperature when the next command arrives, using elapsed time.
        equilibrium = self.target_c if self.loop == "R" else 24.0
        fraction = 1 - math.exp(-elapsed_s / 12)
        self.temperature_c += (equilibrium - self.temperature_c) * fraction
        frame = payload.decode("ascii").strip()
        command, separator, value = frame[3:].partition(",")
        reply = "OK"
        if not frame.startswith("$PC"):
            reply = "ERROR: bad source"
        elif separator:
            if command == "TSP":
                target = float(value)
                self.target_c = target if self.units == "C" else (target - 32) / 1.8
            elif command == "CTL" and value in ("R", "S"):
                self.loop = value
                # Run enables the pump; Stop alone leaves the pump state unchanged.
                if value == "R":
                    self.pump = "ON"
            elif command == "PMP" and value in ("ON", "OFF"):
                if self.loop == "R":
                    reply = "ERROR: stop control loop before pump control"
                else:
                    self.pump = value
            elif command == "SCF" and value in ("C", "F"):
                self.units = value
            elif command == "COL":
                self.coolant = value
            elif command == "DAT":
                self.date = value
            elif command == "TIM":
                self.clock = value
            else:
                reply = "ERROR: invalid write"
        else:
            values = {
                "MOD": "NRC400-T0-00-PC2",
                "SSN": "SIMULATOR-0001",
                "VER": "TEA FIRMWARE VERSION SIM-0.1",
                "CST": f"{self._display_temperature(self.temperature_c):.2f} {self.units}",
                "TSP": f"{self._display_temperature(self.target_c):.1f}",
                "SCF": self.units,
                "CTL": self.loop,
                "PMP": self.pump,
                "LVL": self.level,
                "COL": self.coolant,
                "DAT": self.date,
                "TIM": self.clock,
            }
            reply = values.get(command, "ERROR: unknown command")
        self._buffer.extend((reply + "\r\n").encode("ascii"))
        return len(payload)


class Simulator(SerialTransport):
    """Explicit offline transport; never attempts to open a physical serial port."""

    backend = "simulator"

    def __init__(self, settings: SerialSettings | None = None):
        # Explicitly label the offline peer even if passed a hardware profile.
        settings = replace(settings or SerialSettings(), port="SIMULATOR", usb_serial_number=None)
        super().__init__(settings, SimulatedSerial)
