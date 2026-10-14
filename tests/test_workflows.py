"""Offline CLI/CSV and real Dash HTTP callback checks; no physical port is opened."""

import csv
import json
from dataclasses import replace

import pytest

from tark_nrc import Chiller, CommunicationError, Simulator
from tark_nrc.__main__ import main
from tark_nrc.config import create_chiller
from tark_nrc.gui import DashboardSession, create_app
from tark_nrc.recording import CsvRecorder


def test_cli_simulator_read(capsys):
    assert main(["--simulate", "read"]) == 0
    sample = json.loads(capsys.readouterr().out)
    assert sample["backend"] == "simulator"
    assert sample["setpoint_c"] == 20


def test_cli_requires_backend_and_write_permission(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["read"])
    assert exc.value.code == 2
    assert main(["--simulate", "set", "22"]) == 1
    assert "Writes disabled" in capsys.readouterr().out
    assert main(["--simulate", "--allow-writes", "set", "22"]) == 0


def test_csv_end_to_end_and_existing_file_preserved(tmp_path):
    path = tmp_path / "session.csv"
    args = ["--simulate", "log", "--csv", str(path), "--duration", ".03", "--interval", ".01"]
    assert main(args) == 0
    before = path.read_bytes()
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows and all(row["backend"] == "simulator" for row in rows)
    assert rows[0]["serial_number"] == "SIMULATOR-0001"
    assert all(not row["error"] for row in rows)
    assert main(args) == 1
    assert path.read_bytes() == before


def test_csv_error_row_does_not_repeat_previous_measurement(tmp_path):
    with Chiller(Simulator()) as device:
        path = tmp_path / "failure.csv"
        with CsvRecorder(path, device.identity, "simulator") as recorder:
            recorder.record(device.read_sample())
            recorder.record(error="Disconnected")
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows[1]["temperature_c"] == ""
        assert rows[1]["error"] == "Disconnected"


def test_config_hardware_does_not_silently_use_simulator(tmp_path):
    path = tmp_path / "lab.toml"
    path.write_text('[serial]\nport="COM9999"\n[control]\nallow_writes=false\n')
    device = create_chiller(path)
    assert device.transport.backend == "hardware"
    assert device.transport.settings.port == "COM9999"
    assert not device.allow_writes
    assert device.transport._serial is None


def test_dashboard_actions_invalid_input_and_failure(tmp_path, monkeypatch):
    with Chiller(Simulator(), allow_writes=True) as device:
        with CsvRecorder(tmp_path / "gui.csv", device.identity, "simulator") as recorder:
            session = DashboardSession(device, recorder)
            assert session.refresh()[0] is not None
            assert session.refresh("set", 22)[0].setpoint_c == 22
            assert session.refresh("run")[0].control_loop == "R"
            assert session.refresh("pump-off")[0].control_loop == "R"
            assert not session.failed
            assert session.refresh("stop")[0].control_loop == "S"
            assert session.refresh("pump-off")[0].pump == "OFF"
            assert session.refresh("set", 100)[0].setpoint_c == 22
            assert not session.failed

            def fail():
                raise CommunicationError("Lost serial link")

            monkeypatch.setattr(device, "read_sample", fail)
            assert session.refresh()[0] is None
            assert session.failed
            count = recorder.rows
            assert session.refresh("run")[0] is None
            assert recorder.rows == count


def test_dashboard_csv_failure_stops_session(monkeypatch):
    with Chiller(Simulator()) as device:

        class BrokenRecorder:
            def record(self, *args, **kwargs):
                raise PermissionError("Disk not writable")

        session = DashboardSession(device, BrokenRecorder())
        assert session.refresh()[0].control_loop == "S"
        assert session.failed and len(session.history) == 1


def test_dash_routes_layout_and_packaged_style():
    with Chiller(Simulator()) as device:
        app = create_app(device)
        client = app.server.test_client()
        for route in ["/", "/_dash-layout", "/_dash-dependencies", "/assets/instrument.css"]:
            assert client.get(route).status_code == 200
        layout = client.get("/_dash-layout").get_json()
        assert "Read-only session" in json.dumps(layout)
        assert "SIMULATOR" in json.dumps(layout)


def refresh_dashboard(app, target=None, previous_target=None, action="poll", changed=None):
    """Exercise the real Dash callback dispatch, with a software-test backend."""
    key, callback = next(iter(app.callback_map.items()))
    outputs = [
        {"id": output.component_id, "property": output.component_property}
        for output in callback["output"]
    ]
    inputs = [
        {
            "id": item["id"],
            "property": item["property"],
            "value": 1 if item["id"] == "poll" else None,
        }
        for item in callback["inputs"]
    ]
    response = app.server.test_client().post(
        "/_dash-update-component",
        json={
            "output": key,
            "outputs": outputs,
            "inputs": inputs,
            "state": [
                {"id": "target", "property": "value", "value": target},
                {"id": "reported-target", "property": "data", "value": previous_target},
            ],
            "changedPropIds": changed
            or [f"{action}.{'n_intervals' if action == 'poll' else 'n_clicks'}"],
        },
    )
    assert response.status_code == 200
    return response.get_json()["response"]


def test_dashboard_initializes_live_target_and_preserves_operator_edit():
    with Chiller(Simulator(), allow_writes=True, min_c=12, max_c=20) as device:
        device.set_setpoint(12)
        app = create_app(device)
        first = refresh_dashboard(app)
        assert first["target"]["value"] == 12
        assert not first["target"]["disabled"]
        assert not first["run"]["disabled"]
        later = refresh_dashboard(app, target=13, previous_target=12)
        assert "value" not in later["target"]  # Polling must not overwrite an edit.
        assert len(app.nrc_session.history) == 2


def test_dashboard_failure_locks_new_actions_but_keeps_operator_deactivation(monkeypatch):
    with Chiller(Simulator(), allow_writes=True) as device:
        app = create_app(device)
        refresh_dashboard(app)

        def fail():
            raise CommunicationError("Lost actual serial link (software test)")

        monkeypatch.setattr(device, "read_sample", fail)
        failed = refresh_dashboard(app, target=20)
        assert failed["poll"]["disabled"]
        assert all(failed[key]["disabled"] for key in ("target", "set", "run", "pump-on"))
        assert not failed["stop"]["disabled"] and not failed["pump-off"]["disabled"]
        assert "Session stopped" in failed["notice"]["children"]
        assert len(app.nrc_session.history) == 1


def test_operator_stop_recovery_reidentifies_and_never_replays_run(monkeypatch):
    with Chiller(Simulator(), allow_writes=True) as device:
        session = DashboardSession(device)
        original_read = device.read_sample

        def fail():
            raise CommunicationError("Link failed")

        monkeypatch.setattr(device, "read_sample", fail)
        assert session.refresh()[0] is None and session.failed
        monkeypatch.setattr(device, "read_sample", original_read)
        sample, message = session.refresh("stop")
        assert sample.control_loop == "S"
        assert "recovery verified" in message and session.failed
        frames = device.transport._serial.commands
        assert frames[:3] == [b"$PCMOD\r\n", b"$PCSSN\r\n", b"$PCVER\r\n"]
        assert frames.count(b"$PCCTL,S\r\n") == 1
        assert b"$PCCTL,R\r\n" not in frames
        before = len(frames)
        assert session.refresh("run")[0] is None
        assert len(frames) == before


def test_dashboard_disables_pump_only_controls_while_loop_runs():
    with Chiller(Simulator(), allow_writes=True) as device:
        device.run()
        app = create_app(device)
        state = refresh_dashboard(app)
        assert state["run"]["disabled"]
        assert state["pump-on"]["disabled"] and state["pump-off"]["disabled"]
        assert not state["stop"]["disabled"]


def test_dashboard_follows_external_target_changes_but_preserves_drafts():
    with Chiller(Simulator(), allow_writes=True) as device:
        app = create_app(device)
        first = refresh_dashboard(app)
        assert first["reported-target"]["data"] == 20
        device.transport._serial.target_c = 21  # Stand-in for a front-panel edit.
        synced = refresh_dashboard(app, target=20, previous_target=20)
        assert synced["target"]["value"] == 21
        device.transport._serial.target_c = 22
        draft = refresh_dashboard(app, target=23, previous_target=21)
        assert "value" not in draft["target"]
        assert draft["reported-target"]["data"] == 22
        cleared = refresh_dashboard(app, target=None, previous_target=22)
        assert "value" not in cleared["target"]


def test_invalid_gui_target_keeps_live_state_and_stop_available():
    with Chiller(Simulator(), allow_writes=True) as device:
        device.run()
        app = create_app(device)
        refresh_dashboard(app)
        before = list(device.transport._serial.commands)
        rejected = refresh_dashboard(app, target=100, previous_target=20, action="set")
        assert not rejected["stop"]["disabled"]
        assert not rejected["poll"]["disabled"]
        assert "R / ON" in json.dumps(rejected["telemetry"])
        assert "Setpoint must be" in rejected["notice"]["children"]
        assert all(b"," not in frame for frame in device.transport._serial.commands[len(before) :])
        assert "value" not in rejected["target"]


def test_action_readback_drift_locks_gui_without_replaying_write(monkeypatch):
    with Chiller(Simulator(), allow_writes=True) as device:
        app = create_app(device)
        refresh_dashboard(app)
        original = device.read_sample
        monkeypatch.setattr(device, "read_sample", lambda: replace(original(), control_loop="S"))
        failed = refresh_dashboard(app, target=20, previous_target=20, action="run")
        assert failed["poll"]["disabled"] and failed["run"]["disabled"]
        assert not failed["stop"]["disabled"]
        assert "latest control_loop" in failed["notice"]["children"]
        assert device.transport._serial.commands.count(b"$PCCTL,R\r\n") == 1


def test_csv_failure_preserves_verified_stop_readback():
    class BrokenRecorder:
        def record(self, *args, **kwargs):
            raise OSError("Disk full")

    with Chiller(Simulator(), allow_writes=True) as device:
        device.run()
        session = DashboardSession(device, BrokenRecorder())
        sample, message = session.refresh("stop")
        assert sample.control_loop == "S"
        assert session.failed and "Disk full" in message
        assert device.transport._serial.commands.count(b"$PCCTL,S\r\n") == 1


@pytest.mark.parametrize(
    "action,field,expected",
    [
        ("set", "setpoint_c", 22),
        ("run", "control_loop", "R"),
        ("stop", "control_loop", "S"),
        ("pump-on", "pump", "ON"),
        ("pump-off", "pump", "OFF"),
    ],
)
def test_poll_and_button_same_request_performs_button_once(action, field, expected):
    with Chiller(Simulator(), allow_writes=True) as device:
        if action == "stop":
            device.run()
        elif action == "pump-off":
            device.set_pump(True)
        app = create_app(device)
        refresh_dashboard(app)
        before = len(device.transport._serial.commands)
        refresh_dashboard(
            app, target=22, previous_target=20, changed=["poll.n_intervals", f"{action}.n_clicks"]
        )
        assert getattr(app.nrc_session.history[-1], field) == expected
        writes = [frame for frame in device.transport._serial.commands[before:] if b"," in frame]
        assert len(writes) == 1


def test_simultaneous_activation_controls_are_rejected_without_writes():
    with Chiller(Simulator(), allow_writes=True) as device:
        app = create_app(device)
        before = len(device.transport._serial.commands)
        result = refresh_dashboard(app, target=22, changed=["set.n_clicks", "run.n_clicks"])
        assert "choose one action" in result["notice"]["children"]
        assert all(b"," not in frame for frame in device.transport._serial.commands[before:])


def test_simultaneous_controls_prioritize_stop_without_implicit_pump_off():
    with Chiller(Simulator(), allow_writes=True) as device:
        device.run()
        app = create_app(device)
        before = len(device.transport._serial.commands)
        refresh_dashboard(app, changed=["run.n_clicks", "pump-off.n_clicks", "stop.n_clicks"])
        writes = [frame for frame in device.transport._serial.commands[before:] if b"," in frame]
        assert writes == [b"$PCCTL,S\r\n"]
        assert app.nrc_session.history[-1].control_loop == "S"
        assert app.nrc_session.history[-1].pump == "ON"


def test_simultaneous_controls_preserve_rejected_deactivation_reason():
    with Chiller(Simulator(), allow_writes=True) as device:
        device.run()
        app = create_app(device)
        before = len(device.transport._serial.commands)
        result = refresh_dashboard(app, changed=["set.n_clicks", "pump-off.n_clicks"])
        assert "Stop the control loop" in result["notice"]["children"]
        assert all(b"," not in frame for frame in device.transport._serial.commands[before:])
        assert not result["stop"]["disabled"]


def test_recovery_rejects_replaced_unit_even_without_configured_serial_binding(monkeypatch):
    with Chiller(Simulator(), allow_writes=True) as device:
        session = DashboardSession(device)
        session.failed = True
        original_connect = device.connect

        def connect_replacement():
            original_connect()
            device.identity = replace(device.identity, serial_number="OTHER-UNIT")

        sent = []
        original_close = device.close

        def capture_close():
            if device.transport._serial:
                sent.extend(device.transport._serial.commands)
            original_close()

        monkeypatch.setattr(device, "connect", connect_replacement)
        monkeypatch.setattr(device, "close", capture_close)
        sample, message = session.refresh("stop")
        assert sample is None and session.failed
        assert "Recovery unit changed" in message
        assert device.identity is None
        assert not any(b"," in frame for frame in sent)
