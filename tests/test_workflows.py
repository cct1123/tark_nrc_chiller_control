import csv
import json

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
            assert session.refresh("pump-off")[0] is None
            assert not session.failed
            assert session.refresh("stop")[0].control_loop == "S"
            assert session.refresh("pump-off")[0].pump == "OFF"
            assert session.refresh("set", 100)[0] is None
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
        assert session.refresh()[0] is None
        assert session.failed and not session.history


def test_dash_routes_layout_and_packaged_style():
    with Chiller(Simulator()) as device:
        app = create_app(device)
        client = app.server.test_client()
        for route in ["/", "/_dash-layout", "/_dash-dependencies", "/assets/instrument.css"]:
            assert client.get(route).status_code == 200
        layout = client.get("/_dash-layout").get_json()
        assert "Read-only session" in json.dumps(layout)
        assert "SIMULATOR" in json.dumps(layout)
