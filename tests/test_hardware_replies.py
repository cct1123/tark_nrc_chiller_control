"""Software regression checks derived from COM7 firmware 1.0.21 transcripts.

These scripted exchanges do not establish physical hardware acceptance.
"""

import pytest
from test_command_contract import scripted_device

from tark_nrc import ProtocolError


def test_captured_descriptive_status_replies():
    device, peer = scripted_device("""$PCSCF=Temperature in degrees C
$PCCST=17.74 C
$PCTSP=Coolant Supply Temp Setpoint 12.000000 C
$PCCTL=Stopped
$PCPMP=OFF
$PCLVL=OK
$PCCOL=0
$PCSCF=Temperature in degrees C""")
    with device:
        sample = device.read_sample()
    assert sample.temperature_c == 17.74
    assert sample.setpoint_c == 12
    assert sample.control_loop == "S"
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
    device, peer = scripted_device("$PCLVL=OK\n$PCCTL,R=OK\n$PCCTL=Running")
    with device:
        device.run()
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
def test_captured_running_substates_block_pump_only_changes(reply):
    device, peer = scripted_device(f"$PCCTL={reply}")
    with device, pytest.raises(ValueError, match="Stop the control loop"):
        device.set_pump(False)
    assert not any(b"$PCPMP," in frame for frame in peer.sent)


def test_unverified_running_diagnostic_is_rejected():
    device, _ = scripted_device("$PCCTL=Running... unknown diagnostic")
    with device, pytest.raises(ProtocolError):
        device._choice("CTL", ("R", "S"))


def test_stop_polls_running_transition_without_replaying_write():
    device, peer = scripted_device("""$PCCTL,S=OK
$PCCTL=Running... WasStartControlLoopButtonPressedOff
$PCCTL=Stopped""")
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
