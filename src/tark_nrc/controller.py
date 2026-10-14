"""Small synchronous API. All public temperatures are Celsius."""

import math
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import RLock

from .transport import COMMANDS, ProtocolError, SerialTransport


@dataclass(frozen=True)
class Identity:
    model: str
    serial_number: str
    firmware: str


@dataclass(frozen=True)
class Sample:
    timestamp_utc: str
    backend: str
    temperature_c: float
    setpoint_c: float
    control_loop: str
    pump: str
    level: str
    device_units: str
    coolant: str

    def to_dict(self):
        return asdict(self)


def finite(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


class Chiller:
    def __init__(self, transport: SerialTransport, *, allow_writes=False, min_c=10.0, max_c=40.0):
        self.transport = transport
        self.allow_writes = allow_writes
        self.min_c = finite(min_c, "Minimum")
        self.max_c = finite(max_c, "Maximum")
        if not -5 <= self.min_c < self.max_c <= 50:
            raise ValueError("Setpoint limits must be ordered within -5 to 50 Celsius")
        if type(allow_writes) is not bool:
            raise ValueError("allow_writes must be a boolean")
        self.identity: Identity | None = None
        self._lock = RLock()

    def connect(self):
        with self._lock:
            if self.identity is not None:
                raise RuntimeError("Already connected")
            self.transport.open()
            try:
                model = self.transport.request("MOD")
                if not re.search(r"NRC\s*-?\s*400(?!\d)", model, re.I):
                    raise ProtocolError(f"Expected an NRC400; device reported {model!r}")
                self.identity = Identity(
                    model, self.transport.request("SSN"), self.transport.request("VER")
                )
            except BaseException:
                self.transport.close()
                raise
        return self

    def close(self):
        """Release the port. The physical chiller retains its current state."""
        with self._lock:
            self.transport.close()
            self.identity = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()

    def query(self, command: str) -> str:
        """Read any of the twelve documented commands, including raw date/coolant."""
        if command not in COMMANDS:
            raise ValueError(f"Unknown NRC400 command: {command}")
        with self._lock:
            if self.identity is None:
                raise RuntimeError("Connect first")
            return self.transport.request(command)

    def _choice(self, command, choices):
        value = self.query(command).upper()
        if value not in choices:
            raise ProtocolError(f"{command}: expected {choices}, received {value!r}")
        return value

    def _temperature(self, command, units):
        value = self.query(command)
        match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*([CF])?", value, re.I)
        if not match:
            raise ProtocolError(f"{command}: invalid temperature {value!r}")
        number = float(match[1])
        if not math.isfinite(number):
            raise ProtocolError(f"{command}: nonfinite temperature")
        actual_units = match[2].upper() if match[2] else units
        if actual_units != units:
            raise ProtocolError("Device units changed during the read; read again")
        return number if actual_units == "C" else (number - 32) / 1.8

    def read_temperature(self):
        with self._lock:
            return self._temperature("CST", self._choice("SCF", ("C", "F")))

    def read_setpoint(self):
        with self._lock:
            return self._temperature("TSP", self._choice("SCF", ("C", "F")))

    def read_sample(self) -> Sample:
        with self._lock:
            units = self._choice("SCF", ("C", "F"))
            temp = self._temperature("CST", units)
            target = self._temperature("TSP", units)
            loop = self._choice("CTL", ("R", "S"))
            pump = self._choice("PMP", ("ON", "OFF"))
            level = self._choice("LVL", ("OK", "LOW"))
            coolant = self.query("COL")
            if self._choice("SCF", ("C", "F")) != units:
                raise ProtocolError("Device units changed during the sample; read again")
            return Sample(
                datetime.now(timezone.utc).isoformat(), self.transport.backend,
                temp, target, loop, pump, level, units, coolant,
            )

    def _write(self, command, value):
        if not self.allow_writes:
            raise PermissionError("Writes disabled; enable allow_writes explicitly")
        if self.identity is None:
            raise RuntimeError("Connect first")
        return self.transport.request(command, value)

    def _set(self, command, value, choices, error):
        self._write(command, value)
        if self._choice(command, choices) != value:
            raise ProtocolError(error)

    def set_setpoint(self, target_c: float) -> float:
        target = finite(target_c, "Setpoint")
        if not self.min_c <= target <= self.max_c:
            raise ValueError(f"Setpoint must be {self.min_c:g} to {self.max_c:g} Celsius")
        with self._lock:
            units = self._choice("SCF", ("C", "F"))
            device_target = target if units == "C" else target * 1.8 + 32
            field = f"{device_target:.1f}"
            expected = float(field) if units == "C" else (float(field) - 32) / 1.8
            if not self.min_c <= expected <= self.max_c:
                raise ValueError("Rounded device setpoint would exceed the configured limits")
            self._write("TSP", field)
            reported = self.read_setpoint()
            # Check against the actual transmitted value, including C/F quantization.
            if abs(reported - expected) > 0.001:
                raise ProtocolError(
                    f"Setpoint readback mismatch: requested {target:g}, read {reported:g} C; "
                    "write was not retried"
                )
            return reported

    def run(self):
        with self._lock:
            if self._choice("LVL", ("OK", "LOW")) != "OK":
                raise ValueError("Coolant level LOW; fill and prime at the chiller first")
            self._set("CTL", "R", ("R", "S"),
                      "Run readback did not confirm R; inspect the front panel")

    def stop(self):
        """Stop the control loop; does not claim to stop the pump or remove mains power."""
        with self._lock:
            self._set("CTL", "S", ("R", "S"),
                      "Stop readback did not confirm S; inspect the front panel")

    def set_pump(self, on: bool):
        if type(on) is not bool:
            raise ValueError("Pump state must be a boolean")
        with self._lock:
            if self._choice("CTL", ("R", "S")) != "S":
                raise ValueError("Stop the control loop before changing the pump")
            if on and self._choice("LVL", ("OK", "LOW")) != "OK":
                raise ValueError("Coolant level LOW; do not run the pump dry")
            self._set("PMP", "ON" if on else "OFF", ("ON", "OFF"),
                      "Pump readback mismatch; inspect the front panel")

    def set_units(self, units: str):
        if units not in ("C", "F"):
            raise ValueError("Units must be C or F")
        with self._lock:
            self._set("SCF", units, ("C", "F"), "Unit readback mismatch")

    def set_time(self, value: str):
        pattern = r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
        if not re.fullmatch(pattern, value):
            raise ValueError("Time must be HH:MM:SS, 00:00:00 to 23:59:59")
        with self._lock:
            started = time.monotonic()
            self._write("TIM", value)
            reported = self.query("TIM")
            if not re.fullmatch(pattern, reported):
                raise ProtocolError(f"TIM: invalid time readback {reported!r}")
            def seconds(text):
                hours, minutes, seconds_ = map(int, text.split(":"))
                return hours * 3600 + minutes * 60 + seconds_
            elapsed = time.monotonic() - started
            advance = (seconds(reported) - seconds(value)) % 86400
            # A running clock can advance during the transaction, including across midnight.
            if advance > math.ceil(elapsed) + 1:
                raise ProtocolError(
                    f"Time readback mismatch: requested {value}, read {reported}; "
                    "write was not retried"
                )
            return reported
