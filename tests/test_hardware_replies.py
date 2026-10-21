"""Regression checks for captured firmware replies and operator-reported statuses.

Descriptions were captured on COM7/firmware 1.0.21; QUIT was reported by the
operator on 2026-10-21. These scripted exchanges do not establish physical behavior.
"""

import pytest
from test_command_contract import scripted_device

from tark_nrc import ProtocolError


@pytest.mark.parametrize(
    "loop_reply,expected_loop", [("Stopped", "S"), ("Quit", "QUIT"), ("QUIT", "QUIT")]
)
def test_reported_descriptive_status_replies(loop_reply, expected_loop):
    device, peer = scripted_device(f"""$PCSCF=Temperature in degrees C
$PCCST=17.74 C
$PCTSP=Coolant Supply Temp Setpoint 12.000000 C
$PCCTL={loop_reply}
$PCPMP=OFF
$PCLVL=OK
$PCCOL=0
$PCSCF=Temperature in degrees C""")
    with device:
        sample = device.read_sample()
    assert sample.temperature_c == 17.74
    assert sample.setpoint_c == 12
    assert sample.control_loop == expected_loop
    assert sample.pump == "OFF" and sample.level == "OK"
    assert sample.device_units == "C" and sample.coolant == "0"
    assert not peer.exchanges


@pytest.mark.parametrize("units,value,expected", [("C", "12.000000", 12), ("F", "68.000000", 20)])
def test_descriptive_setpoint_and_units_preserve_celsius_api(units, value, expected):
    device, _ = scripted_device(
        f"$PCSCF=Temperature in degrees {units}\n"
        f"$PCTSP=Coolant Supply Temp Setpoint {value} {units}"
    )
    with device:
        assert device.read_setpoint() == expected


@pytest.mark.parametrize(
    "reply",
    [
        "garbage 12.000000 C",
        "Coolant Supply Temp Setpoint nan C",
        "Coolant Supply Temp Setpoint 12.0 C trailing",
        "Coolant Supply Temp Setpoint 12.0 F",
    ],
)
def test_descriptive_temperature_parser_rejects_unverified_text_and_unit_change(reply):
    device, _ = scripted_device(f"$PCSCF=Temperature in degrees C\n$PCTSP={reply}")
    with device, pytest.raises(ProtocolError):
        device.read_setpoint()


@pytest.mark.parametrize("requested,reply", [("00:44:38", "0:44:38"), ("01:07:01", "1:7:1")])
def test_unpadded_device_clock_fields_are_normalized(requested, reply):
    device, peer = scripted_device(f"$PCTIM,{requested}=OK\n$PCTIM={reply}")
    with device:
        assert device.set_time(requested) == requested
    assert not peer.exchanges


def test_descriptive_running_reply_confirms_control_readback():
    device, peer = scripted_device("$PCCTL,R=OK\n$PCCTL=Running")
    with device:
        device.run()
    assert not peer.exchanges


@pytest.mark.parametrize("reply", ["S", "Stopped", "Quit", "QUIT"])
def test_stop_when_already_stopped_confirms_readback_without_replay(reply):
    device, peer = scripted_device(f"$PCCTL,S=OK\n$PCCTL={reply}")
    with device:
        device.stop()
    assert peer.sent.count(b"$PCCTL,S\r\n") == 1
    assert not peer.exchanges


@pytest.mark.parametrize(
    "reply",
    [
        "Running... WasStartControlLoopButtonPressedOff",
        "Running... MainLoopCheckCoolantSupplyLevelAndPressure",
        "Running... CheckCoolantSupplyTemperature",
        "Running... AnotherFirmwareState_2",
    ],
)
def test_captured_running_substates_remain_readable(reply):
    device, peer = scripted_device(f"$PCCTL={reply}")
    with device:
        assert device._read_choice("CTL", ("R", "S")) == "R"
    assert not any(b"$PCPMP," in frame for frame in peer.sent)


@pytest.mark.parametrize(
    "reply", ["Running... unknown diagnostic", "Quit diagnostic", "QUITTING", "Q"]
)
def test_unverified_loop_diagnostic_is_rejected(reply):
    device, _ = scripted_device(f"$PCCTL={reply}")
    with device, pytest.raises(ProtocolError):
        device._read_choice("CTL", ("R", "S", "QUIT"))


def test_quit_allows_user_commands_without_being_reported_as_success():
    device, peer = scripted_device(
        "$PCCTL=Quit\n$PCPMP,OFF=OK\n$PCPMP=OFF\n$PCCTL,R=OK\n$PCCTL=Quit\n$PCCTL,S=OK\n$PCCTL=S"
    )
    with device:
        assert device.query("CTL") == "Quit"
        device.set_pump(False)
        with pytest.raises(ProtocolError, match="QUIT"):
            device.run()
        device.stop()
    assert peer.sent.count(b"$PCPMP,OFF\r\n") == 1
    assert peer.sent.count(b"$PCCTL,R\r\n") == 1
    assert peer.sent.count(b"$PCCTL,S\r\n") == 1
    assert not peer.exchanges


@pytest.mark.parametrize("terminal", ["Stopped", "Quit"])
def test_stop_accepts_terminal_reply_after_running_without_replaying_write(terminal):
    device, peer = scripted_device(f"""$PCCTL,S=OK
$PCCTL=Running... WasStartControlLoopButtonPressedOff
$PCCTL={terminal}""")
    with device:
        device.stop()
    assert peer.sent.count(b"$PCCTL,S\r\n") == 1
    assert not peer.exchanges


def test_expected_hardware_serial_mismatch_closes_without_control_writes():
    device, peer = scripted_device("$PCCST=17.74 C")
    device.expected_serial_number = "OTHER-UNIT"
    with pytest.raises(ProtocolError, match="Expected serial"):
        device.connect()
    assert device.identity is None and device.transport._serial is None
    assert peer.sent == [b"$PCMOD\r\n", b"$PCSSN\r\n"]
