"""Small synchronous API. All public temperatures are Celsius."""

import math
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import RLock

from .transport import COMMANDS, ProtocolError, SerialTransport


@dataclass(frozen=True)
class Identity:
    """Identity read from the connected unit; never inferred from the COM port."""

    model: str
    serial_number: str
    firmware: str


@dataclass(frozen=True)
class Sample:
    """Sequential device readbacks in Celsius, timestamped after the last query."""

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
    """Validate numeric operator/configuration input without accepting booleans."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


class Chiller:
    """One serial owner; every public write is sent once and verified by a query.

    A failed write/readback blocks subsequent writes until explicit reconnect.
    Reads remain available for diagnosis; close/reconnect never changes equipment state.
    """

    def __init__(
        self,
        transport: SerialTransport,
        *,
        allow_writes=False,
        min_c=10.0,
        max_c=40.0,
        expected_serial_number: str | None = None,
    ):
        self.transport = transport
        self.allow_writes = allow_writes
        self.min_c = finite(min_c, "Minimum")
        self.max_c = finite(max_c, "Maximum")
        if not -5 <= self.min_c < self.max_c <= 50:
            raise ValueError("Setpoint limits must be ordered within -5 to 50 Celsius")
        if type(allow_writes) is not bool:
            raise ValueError("allow_writes must be a boolean")
        if expected_serial_number is not None and (
            not isinstance(expected_serial_number, str) or not expected_serial_number.strip()
        ):
            raise ValueError("expected_serial_number must be a nonempty string or None")
        self.expected_serial_number = expected_serial_number
        self.identity: Identity | None = None
        self._write_uncertain = False
        self._lock = RLock()

    def connect(self):
        """Open and identify the unit using queries only; reset the write fault on success."""
        with self._lock:
            if self.identity is not None:
                raise RuntimeError("Already connected")
            self.transport.open()
            try:
                model = self.transport.request("MOD")
                if not re.search(r"NRC\s*-?\s*400(?!\d)", model, re.I):
                    raise ProtocolError(f"Expected an NRC400; device reported {model!r}")
                serial_number = self.transport.request("SSN")
                if (
                    self.expected_serial_number is not None
                    and serial_number != self.expected_serial_number
                ):
                    raise ProtocolError(
                        f"Expected serial {self.expected_serial_number!r}; "
                        f"device reported {serial_number!r}"
                    )
                self.identity = Identity(model, serial_number, self.transport.request("VER"))
                self._write_uncertain = False
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
        return self._choice_value(command, self.query(command), choices)

    @staticmethod
    def _choice_value(command, reply, choices):
        value = reply.upper()
        aliases = {
            "SCF": {"TEMPERATURE IN DEGREES C": "C", "TEMPERATURE IN DEGREES F": "F"},
            "CTL": {
                "STOPPED": "S",
                "RUNNING": "R",
            },
        }
        value = aliases.get(command, {}).get(value, value)
        # Firmware 1.0.21 reports its running substate as an identifier. This is
        # reported controller state, never proof of physical cooling/circulation.
        if command == "CTL" and re.fullmatch(r"RUNNING\.\.\.\s+[A-Z][A-Z0-9_]*", value):
            value = "R"
        if value not in choices:
            raise ProtocolError(f"{command}: expected {choices}, received {value!r}")
        return value

    def _temperature(self, command, units):
        value = self.query(command)
        prefix = r"(?:Coolant Supply Temp Setpoint\s+)?" if command == "TSP" else ""
        match = re.fullmatch(prefix + r"([+-]?\d+(?:\.\d+)?)\s*([CF])?", value, re.I)
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
        """Read supply temperature in Celsius, checking the device's display units."""
        with self._lock:
            return self._temperature("CST", self._choice("SCF", ("C", "F")))

    def read_setpoint(self):
        """Read the actual target in Celsius, rather than returning a cached request."""
        with self._lock:
            return self._temperature("TSP", self._choice("SCF", ("C", "F")))

    def read_sample(self) -> Sample:
        """Read current temperature/target/states; reject units changing mid-sample."""
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
                datetime.now(timezone.utc).isoformat(),
                self.transport.backend,
                temp,
                target,
                loop,
                pump,
                level,
                units,
                coolant,
            )

    @contextmanager
    def _verified_write(self, command, value):
        """Send once; the caller must verify inside this block before returning.

        The acknowledgement alone is insufficient. Any exchange/readback failure
        latches the write guard because the physical change may already have applied.
        """
        if not self.allow_writes:
            raise PermissionError("Writes disabled; enable allow_writes explicitly")
        if self.identity is None:
            raise RuntimeError("Connect first")
        if self._write_uncertain:
            raise ProtocolError("Previous write unverified; inspect the panel and close/reconnect")
        try:
            self.transport.request(command, value)
            yield
        except BaseException:
            # Interrupts also leave the physical outcome uncertain after TX.
            self._write_uncertain = True
            raise

    def _set(self, command, value, choices, error):
        """Verify an enum write; only Stop permits bounded transitional reads."""
        with self._verified_write(command, value):
            deadline = time.monotonic() + self.transport.settings.timeout_s
            while True:
                reported = self._choice(command, choices)
                if reported == value:
                    return
                # A running readback can precede Stop taking effect. Poll queries
                # only; never resend the write, even when the deadline expires.
                stopping = command == "CTL" and value == "S" and reported == "R"
                remaining = deadline - time.monotonic()
                if stopping and remaining > 0:
                    time.sleep(min(0.1, remaining))
                    if time.monotonic() < deadline:
                        continue
                raise ProtocolError(error)

    def set_setpoint(self, target_c: float) -> float:
        """Write a Celsius target once; return verified, device-quantized Celsius."""
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
            with self._verified_write("TSP", field):
                reported = self.read_setpoint()
                # Compare to the transmitted value, including C/F quantization.
                if abs(reported - expected) > 0.001:
                    raise ProtocolError(
                        f"Setpoint readback mismatch: requested {target:g}, read {reported:g} C; "
                        "write was not retried"
                    )
            return reported

    def run(self):
        """Check level, send Run once, and verify CTL=R; this does not prove flow."""
        with self._lock:
            if self._choice("LVL", ("OK", "LOW")) != "OK":
                raise ValueError("Coolant level LOW; fill and prime at the chiller first")
            self._set(
                "CTL", "R", ("R", "S"), "Run readback did not confirm R; inspect the front panel"
            )

    def stop(self):
        """Stop the control loop; does not claim to stop the pump or remove mains power."""
        with self._lock:
            self._set(
                "CTL", "S", ("R", "S"), "Stop readback did not confirm S; inspect the front panel"
            )

    def set_pump(self, on: bool):
        """With the loop stopped, write/verify PMP; require level OK before ON."""
        if type(on) is not bool:
            raise ValueError("Pump state must be a boolean")
        with self._lock:
            if self._choice("CTL", ("R", "S")) != "S":
                raise ValueError("Stop the control loop before changing the pump")
            if on and self._choice("LVL", ("OK", "LOW")) != "OK":
                raise ValueError("Coolant level LOW; do not run the pump dry")
            self._set(
                "PMP",
                "ON" if on else "OFF",
                ("ON", "OFF"),
                "Pump readback mismatch; inspect the front panel",
            )

    def set_units(self, units: str):
        """Write/verify C or F display units; API temperatures remain Celsius."""
        if units not in ("C", "F"):
            raise ValueError("Units must be C or F")
        with self._lock:
            self._set("SCF", units, ("C", "F"), "Unit readback mismatch")

    def set_time(self, value: str):
        """Write HH:MM:SS once; verify valid readback allowing clock advancement."""
        pattern = r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
        if not re.fullmatch(pattern, value):
            raise ValueError("Time must be HH:MM:SS, 00:00:00 to 23:59:59")
        with self._lock:
            started = time.monotonic()
            with self._verified_write("TIM", value):
                reported = self.query("TIM")
                read_pattern = r"(?:[01]?\d|2[0-3]):[0-5]?\d:[0-5]?\d"
                if not re.fullmatch(read_pattern, reported):
                    raise ProtocolError(f"TIM: invalid time readback {reported!r}")
                reported = ":".join(f"{int(field):02d}" for field in reported.split(":"))

                def seconds(text):
                    hours, minutes, seconds_ = map(int, text.split(":"))
                    return hours * 3600 + minutes * 60 + seconds_

                elapsed = time.monotonic() - started
                advance = (seconds(reported) - seconds(value)) % 86400
                # A running clock can advance, including across midnight.
                if advance > math.ceil(elapsed) + 1:
                    raise ProtocolError(
                        f"Time readback mismatch: requested {value}, read {reported}; "
                        "write was not retried"
                    )
            return reported
