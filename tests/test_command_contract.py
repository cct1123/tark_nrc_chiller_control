"""Literal manual command bodies; framing and replies are fixtures, not bench evidence."""

from collections import deque

import pytest

from tark_nrc import Chiller, ProtocolError, SerialSettings, SerialTransport


def exchanges(transcript):
    return [(body.encode("ascii") + b"\r\n", reply.encode("ascii") + b"\r\n")
            for body, reply in (line.split("=", 1) for line in transcript.splitlines())]


class ScriptedSerial:
    def __init__(self, transcript):
        self.exchanges = deque(exchanges(transcript))
        self.buffer = bytearray()
        self.sent = []

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, frame):
        assert self.exchanges, f"Unexpected command: {frame!r}"
        expected, reply = self.exchanges.popleft()
        assert frame == expected
        self.sent.append(frame)
        self.buffer.extend(reply)
        return len(frame)

    def read(self, size):
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def close(self):
        pass


def scripted_device(transcript):
    identity = "$PCMOD=NRC400-T0-00-PC2\n$PCSSN=TEST-SERIAL\n$PCVER=TEA FIRMWARE VERSION TEST\n"
    peer = ScriptedSerial(identity + transcript)
    link = SerialTransport(SerialSettings(port="SCRIPTED"), lambda **kw: peer)
    return Chiller(link, allow_writes=True), peer


QUERIES = """$PCCOL=20
$PCCST=25.38 C
$PCCTL=S
$PCDAT=10/14/26
$PCLVL=OK
$PCMOD=NRC400-T0-00-PC2
$PCPMP=OFF
$PCSCF=C
$PCSSN=TEST-SERIAL
$PCTIM=12:00:00
$PCTSP=20.0
$PCVER=TEA FIRMWARE VERSION TEST"""


@pytest.mark.parametrize("transcript", QUERIES.splitlines())
def test_all_manual_queries_against_literal_wire_contract(transcript):
    body, reply = transcript.split("=", 1)
    device, peer = scripted_device(transcript)
    with device:
        assert device.query(body[3:]) == reply
    assert not peer.exchanges


@pytest.mark.parametrize("method,args,transcript", [
    ("run", (), "$PCLVL=OK\n$PCCTL,R=ACCEPTED\n$PCCTL=R"),
    ("stop", (), "$PCCTL,S=ACCEPTED\n$PCCTL=S"),
    ("set_pump", (True,), "$PCCTL=S\n$PCLVL=OK\n$PCPMP,ON=ACCEPTED\n$PCPMP=ON"),
    ("set_pump", (False,), "$PCCTL=S\n$PCPMP,OFF=ACCEPTED\n$PCPMP=OFF"),
    ("set_units", ("C",), "$PCSCF,C=ACCEPTED\n$PCSCF=C"),
    ("set_units", ("F",), "$PCSCF,F=ACCEPTED\n$PCSCF=F"),
    ("set_setpoint", (20.0,), "$PCSCF=C\n$PCTSP,20.0=ACCEPTED\n$PCSCF=C\n$PCTSP=20.0"),
    ("set_setpoint", (20.0,), "$PCSCF=F\n$PCTSP,68.0=ACCEPTED\n$PCSCF=F\n$PCTSP=68.0 F"),
    ("set_time", ("18:30:00",), "$PCTIM,18:30:00=ACCEPTED\n$PCTIM=18:30:00"),
])
def test_control_actions_send_correct_fields_and_query_readback(method, args, transcript):
    device, peer = scripted_device(transcript)
    with device:
        getattr(device, method)(*args)
    assert not peer.exchanges


def test_manual_temperature_response_is_parsed():
    device, peer = scripted_device("$PCSCF=C\n$PCCST=25.38 C")
    with device:
        assert device.read_temperature() == 25.38
    assert not peer.exchanges


@pytest.mark.parametrize("reply", ["12:00:00", "garbage", "24:00:00", "18:99:00"])
def test_time_write_rejects_wrong_or_malformed_readback_without_replay(reply):
    device, peer = scripted_device(f"$PCTIM,18:30:00=OK\n$PCTIM={reply}")
    with device, pytest.raises(ProtocolError):
        device.set_time("18:30:00")
    assert not peer.exchanges
    assert peer.sent.count(b"$PCTIM,18:30:00\r\n") == 1


@pytest.mark.parametrize("requested,reported", [
    ("18:30:00", "18:30:01"), ("23:59:59", "00:00:00"),
])
def test_time_readback_allows_clock_tick_and_midnight(requested, reported):
    device, peer = scripted_device(f"$PCTIM,{requested}=OK\n$PCTIM={reported}")
    with device:
        assert device.set_time(requested) == reported
    assert not peer.exchanges
