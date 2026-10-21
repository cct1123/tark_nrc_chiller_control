"""Offline controller input validation, direct control and write/readback checks."""

import math

import pytest

from tark_nrc import Chiller, ProtocolError, Simulator


@pytest.fixture
def chiller():
    with Chiller(Simulator()) as device:
        yield device


def test_connect_only_queries_and_close_does_not_stop(chiller):
    peer = chiller.transport._serial
    assert peer.commands == [b"$PCMOD\r\n", b"$PCSSN\r\n", b"$PCVER\r\n"]
    chiller.run()
    count = len(peer.commands)
    chiller.close()
    assert len(peer.commands) == count
    assert peer.loop == "R" and not peer.is_open


def test_complete_supervised_session(chiller):
    assert chiller.identity.model == "NRC400-T0-00-PC2"
    sample = chiller.read_sample()
    assert sample.backend == "simulator"
    assert sample.level == "OK" and sample.control_loop == "S"
    assert chiller.set_setpoint(21.3) == 21.3
    chiller.set_pump(True)
    chiller.set_pump(False)
    chiller.run()
    assert chiller.read_sample().pump == "ON"
    chiller.stop()
    chiller.set_pump(False)
    assert chiller.read_sample().pump == "OFF"


def test_celsius_api_with_fahrenheit_front_panel(chiller):
    chiller.set_units("F")
    assert chiller.read_temperature() == pytest.approx(24)
    assert chiller.read_setpoint() == pytest.approx(20)
    assert chiller.set_setpoint(23.4) == pytest.approx(23.3888888889)
    assert b"$PCTSP,74.1\r\n" in chiller.transport._serial.commands
    assert chiller.read_sample().device_units == "F"


@pytest.mark.parametrize("value", [math.nan, math.inf, True, None, "", "abc"])
def test_invalid_target_never_writes(chiller, value):
    peer = chiller.transport._serial
    before = list(peer.commands)
    with pytest.raises(ValueError):
        chiller.set_setpoint(value)
    assert peer.commands == before


@pytest.mark.parametrize(
    "action",
    [
        lambda c: c.set_setpoint(20),
        lambda c: c.run(),
        lambda c: c.stop(),
        lambda c: c.set_pump(True),
        lambda c: c.set_units("F"),
        lambda c: c.set_time("12:00:00"),
    ],
)
def test_actions_are_available_without_a_permission_flag(action):
    with Chiller(Simulator()) as device:
        action(device)
        assert sum(b"," in command for command in device.transport._serial.commands) == 1


@pytest.mark.parametrize("action", [lambda c: c.run(), lambda c: c.set_pump(True)])
def test_reported_low_level_does_not_block_user_commands(chiller, action):
    peer = chiller.transport._serial
    peer.level = "LOW"
    before = len(peer.commands)
    action(chiller)
    assert sum(b"," in frame for frame in peer.commands[before:]) == 1


def test_pump_request_is_sent_and_device_rejection_does_not_lock_control(chiller):
    chiller.run()
    with pytest.raises(ProtocolError, match="stop control loop"):
        chiller.set_pump(False)
    assert chiller.transport._serial.commands.count(b"$PCPMP,OFF\r\n") == 1
    chiller.stop()
    assert chiller.read_sample().control_loop == "S"


def test_setpoint_mismatch_does_not_retry(chiller, monkeypatch):
    monkeypatch.setattr(chiller, "read_setpoint", lambda: 22.0)
    with pytest.raises(ProtocolError, match="mismatch"):
        chiller.set_setpoint(21.0)
    writes = [f for f in chiller.transport._serial.commands if b"TSP," in f]
    assert writes == [b"$PCTSP,21.0\r\n"]


def test_time_and_raw_queries(chiller):
    assert chiller.set_time("23:59:59") == "23:59:59"
    assert chiller.query("DAT") == "10/14/26"
    assert chiller.query("COL") == "0"
    with pytest.raises(ValueError):
        chiller.set_time("24:00:00")


def test_invalid_enum_not_reported_as_valid_status(chiller):
    chiller.transport._serial.level = "UNKNOWN"
    with pytest.raises(ProtocolError, match="LVL"):
        chiller.read_sample()


def test_model_mismatch_closes_connection(monkeypatch):
    link = Simulator()
    monkeypatch.setattr(link, "request", lambda *args: "MRC300")
    with pytest.raises(ProtocolError, match="NRC400"):
        Chiller(link).connect()
    assert link._serial is None


def test_display_units_changed_during_sample(chiller, monkeypatch):
    original = chiller.query
    checks = iter(["C", "F"])
    monkeypatch.setattr(
        chiller, "query", lambda code: next(checks) if code == "SCF" else original(code)
    )
    with pytest.raises(ProtocolError, match="units changed"):
        chiller.read_sample()


def test_readback_mismatch_does_not_block_the_next_user_setting(chiller, monkeypatch):
    original = chiller.read_setpoint
    monkeypatch.setattr(chiller, "read_setpoint", lambda: 22)
    with pytest.raises(ProtocolError):
        chiller.set_setpoint(21)
    monkeypatch.setattr(chiller, "read_setpoint", original)
    chiller.set_units("C")
    assert chiller.set_setpoint(21) == 21


def test_interrupted_readback_does_not_lock_future_user_writes(chiller, monkeypatch):
    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(chiller, "read_setpoint", interrupt)
    with pytest.raises(KeyboardInterrupt):
        chiller.set_setpoint(21)
    chiller.set_units("F")
    frames = chiller.transport._serial.commands
    assert frames.count(b"$PCTSP,21.0\r\n") == 1
    assert frames.count(b"$PCSCF,F\r\n") == 1


@pytest.mark.parametrize("target", [-10, 0, 9.9, 40.1, 50])
def test_finite_targets_are_sent_without_an_application_window(chiller, target):
    assert chiller.set_setpoint(target) == target
    assert chiller.transport._serial.commands.count(f"$PCTSP,{target:.1f}\r\n".encode()) == 1


def test_unit_conversion_overflow_is_rejected_without_sending_invalid_numeric_text(chiller):
    chiller.set_units("F")
    before = len(chiller.transport._serial.commands)
    with pytest.raises(ValueError, match="finite number"):
        chiller.set_setpoint(1e308)
    assert all(b"," not in frame for frame in chiller.transport._serial.commands[before:])
