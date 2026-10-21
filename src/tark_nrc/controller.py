"""Read and control one NRC400 chiller. All public temperatures are Celsius.

Start with create_chiller(), then read Chiller's public methods. SerialTransport
handles bytes and replies; Chiller checks their meaning and verifies each setting.
CsvRecorder saves samples, and create_chiller() selects hardware or simulation.
The three-letter command names are documented in docs/protocol.md.
"""

import csv
import logging
import math
import re
import time
import tomllib
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

log = logging.getLogger(__name__)
COMMANDS = frozenset("COL CST CTL DAT LVL MOD PMP SCF SSN TIM TSP VER".split())
WRITABLE = frozenset("COL CTL DAT PMP SCF TIM TSP".split())
ENDINGS = {"cr": b"\r", "lf": b"\n", "crlf": b"\r\n"}


class CommunicationError(RuntimeError):
    """A transaction failed; reconnect explicitly before issuing another command."""


class ProtocolError(RuntimeError):
    """The device reply does not match the documented value format."""


@dataclass(frozen=True)
class SerialSettings:
    """Serial connection options, usually loaded from the TOML [serial] section.

    usb_serial_number selects an adapter even when its COM number changes.
    Queries always need replies. If write_response is False, writes may be silent;
    write_delay_s is the time spent checking for an optional reply before readback.
    """

    port: str = ""
    timeout_s: float = 1.0
    line_ending: str = "crlf"
    write_response: bool = True
    write_delay_s: float = 0.1
    usb_serial_number: str | None = None

    def __post_init__(self):
        if not isinstance(self.port, str):
            raise ValueError("port must be a string naming the verified serial device")
        if self.usb_serial_number is not None and (
            not isinstance(self.usb_serial_number, str) or not self.usb_serial_number.strip()
        ):
            raise ValueError("usb_serial_number must be a nonempty string or None")
        if self.line_ending not in ENDINGS:
            raise ValueError("line_ending must be cr, lf or crlf")
        if (
            isinstance(self.timeout_s, bool)
            or not math.isfinite(self.timeout_s)
            or not 0 < self.timeout_s <= 30
        ):
            raise ValueError("timeout_s must be greater than 0 and at most 30")
        if (
            isinstance(self.write_delay_s, bool)
            or not math.isfinite(self.write_delay_s)
            or not 0 <= self.write_delay_s <= 5
        ):
            raise ValueError("write_delay_s must be between 0 and 5")
        if type(self.write_response) is not bool:
            raise ValueError("write_response must be a boolean")


def encode_command(command: str, data: str | None, ending: str) -> bytes:
    """Build a query such as b'$PCCST\\r\\n', or a write such as b'$PCTSP,20.0\\r\\n'.

    data=None means a query. Reject delimiters in data so one call can send only
    one command. This function prepares bytes; it does not open a connection.
    """
    if command not in COMMANDS:
        raise ValueError(f"Unknown NRC400 command: {command}")
    if data is not None:
        if command not in WRITABLE:
            raise ValueError(f"{command} is read-only")
        if not data or len(data) > 64 or any(c in data for c in "\r\n,$"):
            raise ValueError("Data must be a single nonempty ASCII field")
    text = f"$PC{command}"
    if data is not None:
        text += f",{data}"
    return text.encode("ascii") + ENDINGS[ending]


class SerialTransport:
    """Send commands and read replies over one serial connection.

    Construction opens nothing; call open() and close() explicitly. serial_factory
    normally defaults to serial.Serial. The simulator and tests supply a fake
    serial object with the same read/write methods, keeping this code shared.
    A framing/I/O failure needs close/open; a complete device rejection leaves the
    link usable. Nothing is retried automatically.
    """

    backend = "hardware"

    def __init__(self, settings: SerialSettings, serial_factory=None):
        self.settings = settings
        self._serial_factory = serial_factory
        self._serial = None
        self.port = None
        self._faulted = False

    def open(self):
        """Open the selected OS port at 115200 8N1 without flow control."""
        if self._serial is not None:
            raise RuntimeError("Already connected; close before reconnecting")
        port = self.settings.port
        if self.settings.usb_serial_number is not None:
            from serial.tools.list_ports import comports

            matches = [
                item.device
                for item in comports()
                if item.serial_number == self.settings.usb_serial_number
            ]
            if len(matches) != 1:
                raise CommunicationError(
                    f"USB adapter {self.settings.usb_serial_number!r}: "
                    f"expected one serial port, found {len(matches)}; no port was opened"
                )
            port = matches[0]
        if not port:
            raise ValueError("Provide the verified serial port, for example COM3")
        factory = self._serial_factory
        if factory is None:
            import serial

            factory = serial.Serial
        self._serial = factory(
            port=port,
            baudrate=115200,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=min(0.05, self.settings.timeout_s),
            write_timeout=self.settings.timeout_s,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.port = port
        log.info("Opened %s at 115200 8N1, no flow control", port)
        self._faulted = False

    def close(self):
        """Release the OS port; closing does not send a device command."""
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
                self.port = None

    def request(self, command: str, data: str | None = None) -> str:
        """Send once and consume a framed reply; a raw write does not verify device state.

        Queries require a reply. Explicit optional-write mode observes replies for
        write_delay_s, accepting silence but rejecting errors or incomplete frames.
        Use Chiller's public control methods for validated setting/readback pairs.
        """
        payload = encode_command(command, data, self.settings.line_ending)
        if self._serial is None or self._faulted:
            raise CommunicationError("Disconnected or failed transaction; close and reconnect")
        try:
            self._serial.reset_input_buffer()
            log.debug("TX %r", payload)
            if self._serial.write(payload) != len(payload):
                raise CommunicationError("Incomplete serial write")
            optional_reply = data is not None and not self.settings.write_response
            wait_s = self.settings.write_delay_s if optional_reply else self.settings.timeout_s
            deadline = time.monotonic() + wait_s
            line = bytearray()
            while time.monotonic() < deadline:
                byte = self._serial.read(1)
                if not byte:
                    continue
                if byte in (b"\r", b"\n"):
                    reply = line.decode("ascii").strip()
                    line.clear()
                    if not reply or reply == payload.decode("ascii").strip():
                        continue  # Empty lines and exact command echoes are not responses.
                    log.debug("RX %r", reply)
                    if reply.upper().startswith(("ERR", "INVALID", "NACK", "FAIL")):
                        raise ProtocolError(f"{command}: device rejected request: {reply}")
                    return reply
                line.extend(byte)
                if len(line) > 512:
                    raise CommunicationError("Serial response exceeded 512 bytes")
            if optional_reply and not line:
                return ""
            raise CommunicationError(f"{command}: no complete CR/LF-terminated reply")
        except ProtocolError:
            # A complete device rejection is a usable reply, not a broken link.
            raise
        except (OSError, UnicodeError, RuntimeError) as exc:
            self._faulted = True
            suffix = "; write may have applied; it was not retried" if data is not None else ""
            raise CommunicationError(f"{exc}{suffix}") from exc


@dataclass(frozen=True)
class Identity:
    """Identity read from the connected unit; never inferred from the COM port."""

    model: str
    serial_number: str
    firmware: str


@dataclass(frozen=True)
class Sample:
    """One round of device readings, timestamped after the final query.

    control_loop is R (running), S (stopped), or the preserved firmware status QUIT.
    Stop accepts S/QUIT; neither establishes physical pump-off. Pump is ON/OFF;
    level is OK/LOW.
    device_units records the panel's C/F setting, while temperatures here are
    always Celsius. coolant is the device's raw code, not verified tank contents.
    """

    timestamp_utc: str
    backend: str
    temperature_c: float
    setpoint_c: float
    control_loop: str
    pump: str
    level: str
    device_units: str
    coolant: str

    def to_dict(self) -> dict:
        """Return field names and values for JSON output or one CSV row."""
        return asdict(self)


def finite_number(value, label: str) -> float:
    """Convert input to a float; reject booleans, NaN and infinity with a named error."""
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
    """User API for one connected chiller; setting methods send once, then read back.

    Commands are available whenever connected. Device rejections and mismatched
    readbacks raise errors without locking later user actions. Closing the port
    leaves the equipment running in its current state.

    Typical use: with create_chiller(simulate=True) as chiller: ...
    The with block connects on entry and closes on exit; it never sends Stop.
    """

    def __init__(
        self,
        transport: SerialTransport,
        *,
        expected_serial_number: str | None = None,
    ):
        self.transport = transport
        if expected_serial_number is not None and (
            not isinstance(expected_serial_number, str) or not expected_serial_number.strip()
        ):
            raise ValueError("expected_serial_number must be a nonempty string or None")
        self.expected_serial_number = expected_serial_number
        self.identity: Identity | None = None
        # Public operations call other public operations (read_sample -> query).
        # RLock lets the same thread acquire this lock again without deadlocking.
        self._lock = RLock()

    def connect(self):
        """Open and identify the unit using queries only; send no control commands."""
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

    def _read_choice(self, command: str, choices: tuple[str, ...]) -> str:
        """Read a status, normalize known firmware wording, and require an allowed value."""
        value = self.query(command).upper()
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

    def _read_temperature_c(self, command: str, units: str) -> float:
        """Parse a temperature reply in the expected device units and convert it to Celsius."""
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

    def read_temperature(self) -> float:
        """Read supply temperature in Celsius, checking the device's display units."""
        with self._lock:
            return self._read_temperature_c("CST", self._read_choice("SCF", ("C", "F")))

    def read_setpoint(self) -> float:
        """Read the actual target in Celsius, rather than returning a cached request."""
        with self._lock:
            return self._read_temperature_c("TSP", self._read_choice("SCF", ("C", "F")))

    def read_sample(self) -> Sample:
        """Read a fresh sample; the individual queries are sequential, not simultaneous.

        Check display units before and after reading so a panel change cannot mix
        Celsius and Fahrenheit values into one apparently valid sample.
        """
        with self._lock:
            units = self._read_choice("SCF", ("C", "F"))
            temperature_c = self._read_temperature_c("CST", units)
            setpoint_c = self._read_temperature_c("TSP", units)
            # Preserve QUIT as reported; firmware 1.0.21 returned it after Stop
            # while the operator observed the unit already stopped.
            control_loop = self._read_choice("CTL", ("R", "S", "QUIT"))
            pump = self._read_choice("PMP", ("ON", "OFF"))
            level = self._read_choice("LVL", ("OK", "LOW"))
            coolant = self.query("COL")
            if self._read_choice("SCF", ("C", "F")) != units:
                raise ProtocolError("Device units changed during the sample; read again")
            return Sample(
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                backend=self.transport.backend,
                temperature_c=temperature_c,
                setpoint_c=setpoint_c,
                control_loop=control_loop,
                pump=pump,
                level=level,
                device_units=units,
                coolant=coolant,
            )

    def _write_and_check_choice(self, command, value, choices, error):
        """Write a status setting and verify its accepted readback.

        Stop accepts S or the observed terminal QUIT reply. While still running,
        query until stopped or the deadline; never resend the command.
        """
        if self.identity is None:
            raise RuntimeError("Connect first")
        self.transport.request(command, value)
        accepted = ("S", "QUIT") if command == "CTL" and value == "S" else (value,)
        deadline = time.monotonic() + self.transport.settings.timeout_s
        while True:
            reported = self._read_choice(command, choices)
            if reported in accepted:
                return
            # Poll a running transition only; S/QUIT are terminal Stop replies.
            stopping = command == "CTL" and value == "S" and reported == "R"
            remaining = deadline - time.monotonic()
            if stopping and remaining > 0:
                time.sleep(min(0.1, remaining))
                if time.monotonic() < deadline:
                    continue
            raise ProtocolError(
                f"{error}; last {command} readback was {reported!r}, expected one of {accepted!r}; "
                "write was sent once and was not retried"
            )

    def set_setpoint(self, target_c: float) -> float:
        """Set a Celsius target and return the confirmed target after device rounding.

        We transmit one decimal place in the device's display units. Verify against
        that transmitted value, converted back to Celsius, rather than the original
        input. The device decides which target values it accepts.
        """
        target = finite_number(target_c, "Setpoint")
        with self._lock:
            units = self._read_choice("SCF", ("C", "F"))
            device_target = target if units == "C" else target * 1.8 + 32
            device_target = finite_number(device_target, "Setpoint in device units")
            field = f"{device_target:.1f}"
            expected = float(field) if units == "C" else (float(field) - 32) / 1.8
            self.transport.request("TSP", field)
            reported = self.read_setpoint()
            # Compare to the transmitted value, including C/F quantization.
            if abs(reported - expected) > 0.001:
                raise ProtocolError(
                    f"Setpoint readback mismatch: requested {target:g}, read {reported:g} C; "
                    "write was not retried"
                )
            return reported

    def run(self):
        """Send Run once and verify CTL=R; the device decides whether it can run."""
        with self._lock:
            self._write_and_check_choice(
                "CTL", "R", ("R", "S"), "Run readback did not confirm R; inspect the front panel"
            )

    def stop(self):
        """Send Stop once and accept S/QUIT; does not imply pump-off or power removal."""
        with self._lock:
            self._write_and_check_choice(
                "CTL",
                "S",
                ("R", "S", "QUIT"),
                "Stop readback did not confirm S/QUIT; inspect the front panel",
            )

    def set_pump(self, on: bool):
        """Send the chosen pump state once and verify its readback, without prerequisites."""
        if type(on) is not bool:
            raise ValueError("Pump state must be a boolean")
        with self._lock:
            self._write_and_check_choice(
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
            self._write_and_check_choice("SCF", units, ("C", "F"), "Unit readback mismatch")

    def set_time(self, value: str):
        """Write HH:MM:SS once; verify valid readback allowing clock advancement."""
        pattern = r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
        if not re.fullmatch(pattern, value):
            raise ValueError("Time must be HH:MM:SS, 00:00:00 to 23:59:59")
        with self._lock:
            if self.identity is None:
                raise RuntimeError("Connect first")
            started = time.monotonic()
            self.transport.request("TIM", value)
            reported = self.query("TIM")
            read_pattern = r"(?:[01]?\d|2[0-3]):[0-5]?\d:[0-5]?\d"
            if not re.fullmatch(read_pattern, reported):
                raise ProtocolError(f"TIM: invalid time readback {reported!r}")
            reported = ":".join(f"{int(field):02d}" for field in reported.split(":"))

            def seconds_since_midnight(text):
                hours, minutes, seconds_ = map(int, text.split(":"))
                return hours * 3600 + minutes * 60 + seconds_

            elapsed = time.monotonic() - started
            advance = (seconds_since_midnight(reported) - seconds_since_midnight(value)) % 86400
            # A running clock can advance, including across midnight.
            if advance > math.ceil(elapsed) + 1:
                raise ProtocolError(
                    f"Time readback mismatch: requested {value}, read {reported}; "
                    "write was not retried"
                )
            return reported


class CsvRecorder:
    """Save samples and device identity to a new CSV; never overwrite an existing file.

    Use as a with block, or call close() when finished. Each row is flushed so
    earlier measurements remain readable after a later communication/disk failure.
    """

    def __init__(self, path: str | Path, identity: Identity, backend: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", newline="", encoding="utf-8")  # x = create only if new
        self._identity = asdict(identity)
        self._backend = backend
        columns = [f.name for f in fields(Sample)] + list(self._identity) + ["error"]
        self._writer = csv.DictWriter(self._file, fieldnames=columns)
        self.rows = 0
        try:
            self._writer.writeheader()
            self._file.flush()
        except BaseException:
            self._file.close()
            raise

    def record(self, sample: Sample | None = None, *, error: str = ""):
        """Append a sample or an error with blank measurements, never cached values."""
        if sample is None:
            row = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "backend": self._backend,
            }
        else:
            row = sample.to_dict()
        row.update(self._identity)
        row["error"] = error
        self._writer.writerow(row)
        self._file.flush()
        self.rows += 1

    def close(self):
        """Close the CSV; the chiller connection is owned separately."""
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def create_chiller(config_path=None, *, simulate=False, port=None):
    """Build a controller without connecting; use its with block or call connect().

    config_path optionally names a TOML file with [serial] and [control] sections.
    port overrides its port/USB adapter selection. simulate=True uses memory
    instead of hardware. All control methods are available after connection;
    hardware needs a port or USB adapter selection.
    """
    config = {}
    if config_path is not None:
        with Path(config_path).open("rb") as stream:
            config = tomllib.load(stream)
    unknown = set(config) - {"serial", "control"}
    if unknown:
        raise ValueError(f"Unknown configuration sections: {sorted(unknown)}")
    serial_config = dict(config.get("serial", {}))
    control = dict(config.get("control", {}))
    unknown_control = set(control) - {"expected_serial_number"}
    if unknown_control:
        raise ValueError(
            f"Unknown control settings: {sorted(unknown_control)}; "
            "only expected_serial_number is supported"
        )
    if port is not None:
        serial_config["port"] = port
        serial_config.pop("usb_serial_number", None)
    if simulate:
        serial_config["port"] = "SIMULATOR"
    settings = SerialSettings(**serial_config)
    if simulate:
        # Simulator imports this module's transport; import it here to avoid a cycle.
        from .simulator import Simulator

        transport = Simulator(settings)
    else:
        transport = SerialTransport(settings)
    return Chiller(transport, **control)
