"""Literal byte framing and failure tests using an offline serial peer."""

import time

import pytest

from tark_nrc import CommunicationError, SerialSettings, SerialTransport
from tark_nrc.transport import encode_command


class SerialPeer:
    def __init__(self, reply):
        self.reply = reply
        self.buffer = bytearray()
        self.frames = []

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, payload):
        self.frames.append(payload)
        self.buffer.extend(self.reply)
        return len(payload)

    def read(self, size):
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        if not data:
            time.sleep(0.001)
        return data

    def close(self):
        pass


def link_for(reply, **settings):
    peer = SerialPeer(reply)
    link = SerialTransport(
        SerialSettings(port="TEST", timeout_s=0.02, **settings), lambda **kw: peer
    )
    link.open()
    return link, peer


@pytest.mark.parametrize("ending,bytes_", [("cr", b"\r"), ("lf", b"\n"), ("crlf", b"\r\n")])
def test_manual_command_examples_and_write_format(ending, bytes_):
    assert encode_command("CST", None, ending) == b"$PCCST" + bytes_
    assert encode_command("VER", None, ending) == b"$PCVER" + bytes_
    assert encode_command("TSP", "20.0", ending) == b"$PCTSP,20.0" + bytes_


@pytest.mark.parametrize("reply", [b"25.38 C\r", b"25.38 C\n", b"\r\n$PCCST\r\n25.38 C\r\n"])
def test_bare_response_and_echo(reply):
    link, _ = link_for(reply)
    assert link.request("CST") == "25.38 C"


@pytest.mark.parametrize("reply", [b"", b"25.38 C", b"\xff\n", b"x" * 513 + b"\n", b"ERROR: bad\n"])
def test_failed_exchange_blocks_following_request(reply):
    link, peer = link_for(reply)
    with pytest.raises(CommunicationError):
        link.request("CST")
    count = len(peer.frames)
    with pytest.raises(CommunicationError, match="reconnect"):
        link.request("CST")
    assert len(peer.frames) == count


def test_unacknowledged_write_is_not_replayed():
    link, peer = link_for(b"")
    with pytest.raises(CommunicationError, match="may have applied"):
        link.request("CTL", "R")
    assert peer.frames == [b"$PCCTL,R\r\n"]


def test_explicit_no_write_reply_mode():
    link, peer = link_for(b"", write_response=False, write_delay_s=0)
    assert link.request("TSP", "20.0") == ""
    peer.reply = b"20.0\r\n"
    assert link.request("TSP") == "20.0"


@pytest.mark.parametrize("reply", [b"ERROR: rejected\n", b"\xff\n", b"partial"])
def test_optional_write_reply_does_not_discard_errors_or_partial_frames(reply):
    link, peer = link_for(reply, write_response=False, write_delay_s=0.02)
    with pytest.raises(CommunicationError, match="may have applied"):
        link.request("TSP", "12.0")
    with pytest.raises(CommunicationError, match="reconnect"):
        link.request("TSP")
    assert peer.frames == [b"$PCTSP,12.0\r\n"]


def test_optional_write_reply_is_consumed_before_readback():
    link, peer = link_for(b"OK\n", write_response=False, write_delay_s=0.02)
    assert link.request("TSP", "12.0") == "OK"
    assert not peer.buffer
    peer.reply = b"Coolant Supply Temp Setpoint 12.000000 C\n"
    assert link.request("TSP") == "Coolant Supply Temp Setpoint 12.000000 C"


@pytest.mark.parametrize("data", ["", "20\r$PCCTL,R", "20,30", "$PCCTL", "20\n"])
def test_data_cannot_inject_second_command(data):
    with pytest.raises(ValueError):
        encode_command("TSP", data, "crlf")


def test_actual_serial_open_parameters_and_lazy_construction():
    calls = []
    peer = SerialPeer(b"NRC400\r\n")
    link = SerialTransport(SerialSettings(port="COM7"), lambda **kw: calls.append(kw) or peer)
    assert not calls
    link.open()
    settings = calls[0]
    assert (
        settings["baudrate"],
        settings["bytesize"],
        settings["parity"],
        settings["stopbits"],
    ) == (115200, 8, "N", 1)
    assert not any(settings[k] for k in ["xonxoff", "rtscts", "dsrdtr"])


def test_partial_serial_write_is_failure(monkeypatch):
    link, peer = link_for(b"20.0\n")
    monkeypatch.setattr(peer, "write", lambda payload: 2)
    with pytest.raises(CommunicationError, match="Incomplete"):
        link.request("TSP", "20.0")


@pytest.mark.parametrize(
    "settings",
    [
        {"port": True},
        {"port": 7},
        {"port": None},
        {"timeout_s": True},
        {"write_delay_s": False},
    ],
)
def test_serial_settings_reject_implicit_port_numbers_and_boolean_timings(settings):
    with pytest.raises(ValueError):
        SerialSettings(**settings)
