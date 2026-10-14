"""Memory-only serial peer. It exercises the real codec, not a separate driver."""

import math
import time

from .transport import SerialSettings, SerialTransport


class SimulatedSerial:
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
        self._last = time.monotonic()

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self._buffer.clear()

    def read(self, size=1):
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        if not data:
            time.sleep(0.001)
        return data

    def _display(self, c):
        return c if self.units == "C" else c * 1.8 + 32

    def write(self, payload):
        self.commands.append(payload)
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        equilibrium = self.target_c if self.loop == "R" else 24.0
        self.temperature_c += (equilibrium - self.temperature_c) * (1 - math.exp(-dt / 12))
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
                if value == "R":
                    self.pump = "ON"
            elif command == "PMP" and value in ("ON", "OFF"):
                if self.loop == "R":
                    reply = "ERROR: stop control loop before pump control"
                else:
                    self.pump = value
            elif command in ("SCF", "COL", "DAT", "TIM") and (
                command != "SCF" or value in ("C", "F")
            ):
                field = {"SCF": "units", "COL": "coolant", "DAT": "date", "TIM": "clock"}
                setattr(self, field[command], value)
            else:
                reply = "ERROR: invalid write"
        else:
            values = {
                "MOD": "NRC400-T0-00-PC2", "SSN": "SIMULATOR-0001",
                "VER": "TEA FIRMWARE VERSION SIM-0.1",
                "CST": f"{self._display(self.temperature_c):.2f} {self.units}",
                "TSP": f"{self._display(self.target_c):.1f}",
                "SCF": self.units, "CTL": self.loop, "PMP": self.pump, "LVL": self.level,
                "COL": self.coolant, "DAT": self.date, "TIM": self.clock,
            }
            reply = values.get(command, "ERROR: unknown command")
        self._buffer.extend((reply + "\r\n").encode("ascii"))
        return len(payload)


class Simulator(SerialTransport):
    backend = "simulator"

    def __init__(self, settings: SerialSettings | None = None):
        super().__init__(settings or SerialSettings(port="SIMULATOR"), SimulatedSerial)

    def open(self):
        # SerialTransport still checks the port; simulator never opens an OS device.
        if not self.settings.port:
            from dataclasses import replace

            self.settings = replace(self.settings, port="SIMULATOR")
        super().open()
