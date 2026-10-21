"""Literal byte framing and failure tests using an offline serial peer."""

import time
from types import SimpleNamespace

import pytest

from tark_nrc import CommunicationError, ProtocolError, SerialSettings, SerialTransport
from tark_nrc.controller import encode_command


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


@pytest.mark.parametrize("reply", [b"", b"25.38 C", b"\xff\n", b"x" * 513 + b"\n"])
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


@pytest.mark.parametrize("reply", [b"\xff\n", b"partial"])
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


def test_usb_adapter_binding_follows_com_number_without_opening_other_adapters(monkeypatch):
    from serial.tools import list_ports

    ports = [
        SimpleNamespace(device="COM9", serial_number="OTHER-ADAPTER"),
        SimpleNamespace(device="COM12", serial_number="FTB8VKKUA"),
    ]
    monkeypatch.setattr(list_ports, "comports", lambda: ports)
    calls = []
    link = SerialTransport(
        SerialSettings(port="COM10", usb_serial_number="FTB8VKKUA"),
        lambda **kw: calls.append(kw) or SerialPeer(b""),
    )
    assert not calls and link.port is None
    link.open()
    assert len(calls) == 1 and calls[0]["port"] == "COM12"
    assert calls[0]["baudrate"] == 115200
    assert not any(calls[0][key] for key in ("xonxoff", "rtscts", "dsrdtr"))
    assert link.port == "COM12"
    link.close()
    assert link.port is None
    ports[1].device = "COM14"
    link.open()
    assert calls[-1]["port"] == "COM14"
    link.close()


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_usb_adapter_never_falls_back_to_configured_port(monkeypatch, count):
    from serial.tools import list_ports

    monkeypatch.setattr(
        list_ports,
        "comports",
        lambda: [
            SimpleNamespace(device=f"COM{index}", serial_number="BOUND") for index in range(count)
        ],
    )
    calls = []
    link = SerialTransport(
        SerialSettings(port="COM10", usb_serial_number="BOUND"),
        lambda **kw: calls.append(kw),
    )
    with pytest.raises(CommunicationError, match=f"found {count}"):
        link.open()
    assert not calls and link.port is None


def test_simulation_ignores_physical_usb_adapter_binding(monkeypatch):
    from serial.tools import list_ports

    from tark_nrc import Chiller, Simulator

    def forbidden():
        pytest.fail("Simulator must not enumerate physical USB adapters")

    monkeypatch.setattr(list_ports, "comports", forbidden)
    with Chiller(Simulator(SerialSettings(port="COM10", usb_serial_number="BOUND"))) as device:
        assert device.identity.serial_number == "SIMULATOR-0001"
        assert device.transport.port == "SIMULATOR"


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
        {"usb_serial_number": ""},
        {"usb_serial_number": "  "},
        {"usb_serial_number": True},
        {"usb_serial_number": 123},
        {"timeout_s": True},
        {"write_delay_s": False},
    ],
)
def test_serial_settings_reject_implicit_port_numbers_and_boolean_timings(settings):
    with pytest.raises(ValueError):
        SerialSettings(**settings)


@pytest.mark.parametrize("write_response", [True, False])
def test_complete_device_rejection_does_not_block_next_request(write_response):
    link, peer = link_for(b"ERROR: rejected\n", write_response=write_response, write_delay_s=0.02)
    with pytest.raises(ProtocolError, match="device rejected"):
        link.request("PMP", "OFF")
    peer.reply = b"S\n"
    assert link.request("CTL") == "S"
    assert peer.frames == [b"$PCPMP,OFF\r\n", b"$PCCTL\r\n"]
