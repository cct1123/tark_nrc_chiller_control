"""NRC400 ASCII transactions. Framing assumptions are configurable; see protocol.md."""

import logging
import math
import time
from dataclasses import dataclass

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
    port: str = ""
    timeout_s: float = 1.0
    line_ending: str = "crlf"
    write_response: bool = True
    write_delay_s: float = 0.1

    def __post_init__(self):
        if self.line_ending not in ENDINGS:
            raise ValueError("line_ending must be cr, lf or crlf")
        if not math.isfinite(self.timeout_s) or not 0 < self.timeout_s <= 30:
            raise ValueError("timeout_s must be greater than 0 and at most 30")
        if not math.isfinite(self.write_delay_s) or not 0 <= self.write_delay_s <= 5:
            raise ValueError("write_delay_s must be between 0 and 5")
        if type(self.write_response) is not bool:
            raise ValueError("write_response must be a boolean")


def encode_command(command: str, data: str | None, ending: str) -> bytes:
    if command not in COMMANDS:
        raise ValueError(f"Unknown NRC400 command: {command}")
    if data is not None:
        if command not in WRITABLE:
            raise ValueError(f"{command} is read-only")
        if not data or len(data) > 64 or any(c in data for c in "\r\n,$"):
            raise ValueError("Data must be a single nonempty ASCII field")
    text = f"$PC{command}" + (f",{data}" if data is not None else "")
    return text.encode("ascii") + ENDINGS[ending]


class SerialTransport:
    """Own one serial port. Construction opens nothing; no retry or reconnect loop."""

    backend = "hardware"

    def __init__(self, settings: SerialSettings, serial_factory=None):
        self.settings = settings
        self._factory = serial_factory
        self._serial = None
        self._faulted = False

    def open(self):
        if self._serial is not None:
            raise RuntimeError("Already connected; close before reconnecting")
        if not self.settings.port:
            raise ValueError("Provide the verified serial port, for example COM3")
        factory = self._factory
        if factory is None:
            import serial

            factory = serial.Serial
        self._serial = factory(
            port=self.settings.port, baudrate=115200, bytesize=8, parity="N", stopbits=1,
            timeout=min(0.05, self.settings.timeout_s),
            write_timeout=self.settings.timeout_s,
            xonxoff=False, rtscts=False, dsrdtr=False,
        )
        self._faulted = False

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def request(self, command: str, data: str | None = None) -> str:
        payload = encode_command(command, data, self.settings.line_ending)
        if self._serial is None or self._faulted:
            raise CommunicationError("Disconnected or failed transaction; close and reconnect")
        try:
            self._serial.reset_input_buffer()
            log.debug("TX %r", payload)
            if self._serial.write(payload) != len(payload):
                raise CommunicationError("Incomplete serial write")
            if data is not None and not self.settings.write_response:
                time.sleep(self.settings.write_delay_s)
                return ""
            deadline = time.monotonic() + self.settings.timeout_s
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
                    raise ProtocolError("Serial response exceeded 512 bytes")
            raise CommunicationError(f"{command}: no complete CR/LF-terminated reply")
        except (OSError, UnicodeError, RuntimeError) as exc:
            self._faulted = True
            suffix = "; write may have applied; it was not retried" if data is not None else ""
            raise CommunicationError(f"{exc}{suffix}") from exc
