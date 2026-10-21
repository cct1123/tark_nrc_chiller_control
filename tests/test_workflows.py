"""Offline CLI/CSV and real Dash HTTP callback checks; no physical port is opened."""

import csv
import json
from dataclasses import replace

import pytest

from tark_nrc import Chiller, CommunicationError, Simulator
from tark_nrc.controller import CsvRecorder, create_chiller
from tark_nrc.gui import DashboardSession, create_app, main


def test_cli_simulator_read(capsys):
    assert main(["--simulate", "read"]) == 0
    sample = json.loads(capsys.readouterr().out)
    assert sample["backend"] == "simulator"
    assert sample["setpoint_c"] == 20


@pytest.mark.parametrize(
    "failure,exit_code", [(RuntimeError("Lost link"), 1), (KeyboardInterrupt(), 0)]
)
def test_cli_closes_connection_after_command_failure_or_interrupt(monkeypatch, failure, exit_code):
    import tark_nrc.gui as gui

    device = create_chiller(simulate=True)
    sent = []

    def fail_command(chiller, args):
        assert chiller.identity is not None
        sent.extend(chiller.transport._serial.commands)
        raise failure

    monkeypatch.setattr(gui, "create_chiller", lambda *args, **kwargs: device)
    monkeypatch.setattr(gui, "execute_command", fail_command)
    assert main(["--simulate", "read"]) == exit_code
    assert device.identity is None and device.transport._serial is None
    assert sent == [b"$PCMOD\r\n", b"$PCSSN\r\n", b"$PCVER\r\n"]


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
    path.write_text('[serial]\nport="COM9999"\n')
    device = create_chiller(path)
    assert device.transport.backend == "hardware"
    assert device.transport.settings.port == "COM9999"
    assert device.transport._serial is None


def test_config_loads_usb_binding_and_explicit_port_overrides_it(tmp_path):
    path = tmp_path / "adapter.toml"
    path.write_text('[serial]\nport="COM10"\nusb_serial_number="FTB8VKKUA"\n')
    device = create_chiller(path)
    assert device.transport.settings.usb_serial_number == "FTB8VKKUA"
    assert device.transport._serial is None
    override = create_chiller(path, port="COM9")
    assert override.transport.settings.port == "COM9"
    assert override.transport.settings.usb_serial_number is None


def test_dash_routes_layout_and_packaged_style():
    with Chiller(Simulator()) as device:
        app = create_app(device)
        client = app.server.test_client()
        for route in ["/", "/_dash-layout", "/_dash-dependencies", "/assets/instrument.css"]:
            assert client.get(route).status_code == 200
        layout = client.get("/_dash-layout").get_json()
        assert "Manual control" in json.dumps(layout)
        assert "SIMULATOR" in json.dumps(layout)


def refresh_dashboard(app, target=None, previous_target=None, action="poll", changed=None):
    """Exercise the real Dash callback dispatch, with a software-test backend."""
    payload = dashboard_payload(app, target, previous_target, action, changed)
    response = app.server.test_client().post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    # Model the browser applying returned labels before the next click.
    app._browser_button_labels = {
        "loop": result["loop"]["children"],
        "pump": result["pump"]["children"],
    }
    return result


def dashboard_payload(app, target=None, previous_target=None, action="poll", changed=None):
    """Construct a callback request for either the test client or a live local server."""
    labels = getattr(app, "_browser_button_labels", {"loop": "Run loop", "pump": "Pump on"})
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
    return {
        "output": key,
        "outputs": outputs,
        "inputs": inputs,
        "state": [
            {"id": "target", "property": "value", "value": target},
            {"id": "reported-target", "property": "data", "value": previous_target},
            {"id": "loop", "property": "children", "value": labels["loop"]},
            {"id": "pump", "property": "children", "value": labels["pump"]},
        ],
        "changedPropIds": changed
        or [f"{action}.{'n_intervals' if action == 'poll' else 'n_clicks'}"],
    }


def test_dashboard_initializes_live_target_and_preserves_operator_edit():
    with Chiller(Simulator()) as device:
        device.set_setpoint(12)
        app = create_app(device)
        first = refresh_dashboard(app)
        assert first["target"]["value"] == 12
        assert not first["target"]["disabled"]
        assert not first["loop"]["disabled"]
        later = refresh_dashboard(app, target=13, previous_target=12)
        assert "value" not in later["target"]  # Polling must not overwrite an edit.
        assert len(app.nrc_session.history) == 2


def test_dashboard_follows_external_target_changes_but_preserves_drafts():
    with Chiller(Simulator()) as device:
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
    with Chiller(Simulator()) as device:
        device.run()
        app = create_app(device)
        refresh_dashboard(app)
        before = list(device.transport._serial.commands)
        rejected = refresh_dashboard(app, target=None, previous_target=20, action="set")
        assert not rejected["loop"]["disabled"]
        assert not rejected["poll"]["disabled"]
        assert "R / ON" in json.dumps(rejected["telemetry"])
        assert "finite number" in rejected["notice"]["children"]
        assert all(b"," not in frame for frame in device.transport._serial.commands[len(before) :])
        assert "value" not in rejected["target"]


@pytest.mark.parametrize(
    "button,initial,field,expected",
    [
        ("set", None, "setpoint_c", 22),
        ("loop", "S", "control_loop", "R"),
        ("loop", "R", "control_loop", "S"),
        ("pump", "OFF", "pump", "ON"),
        ("pump", "ON", "pump", "OFF"),
    ],
)
def test_poll_and_toggle_same_request_sends_the_displayed_command_once(
    button, initial, field, expected
):
    with Chiller(Simulator()) as device:
        if button == "loop" and initial == "R":
            device.run()
        elif button == "pump" and initial == "ON":
            device.set_pump(True)
        app = create_app(device)
        refresh_dashboard(app)
        before = len(device.transport._serial.commands)
        refresh_dashboard(
            app, target=22, previous_target=20, changed=["poll.n_intervals", f"{button}.n_clicks"]
        )
        assert getattr(app.nrc_session.history[-1], field) == expected
        assert sum(b"," in frame for frame in device.transport._serial.commands[before:]) == 1


def test_simultaneous_activation_controls_are_rejected_without_writes():
    with Chiller(Simulator()) as device:
        app = create_app(device)
        before = len(device.transport._serial.commands)
        result = refresh_dashboard(app, target=22, changed=["set.n_clicks", "loop.n_clicks"])
        assert "choose one action" in result["notice"]["children"]
        assert all(b"," not in frame for frame in device.transport._serial.commands[before:])


def test_simultaneous_stop_and_pump_off_prioritize_stop_without_implicit_pump_write():
    with Chiller(Simulator()) as device:
        device.run()
        app = create_app(device)
        initial = refresh_dashboard(app)
        assert initial["loop"]["children"] == "Stop loop"
        assert initial["pump"]["children"] == "Pump off"
        before = len(device.transport._serial.commands)
        refresh_dashboard(app, changed=["loop.n_clicks", "pump.n_clicks"])
        writes = [frame for frame in device.transport._serial.commands[before:] if b"," in frame]
        assert writes == [b"$PCCTL,S\r\n"]
        assert app.nrc_session.history[-1].pump == "ON"


def test_dashboard_waits_for_connect_and_opens_csv_after_identification(tmp_path):
    device = Chiller(Simulator())
    path = tmp_path / "connected.csv"
    app = create_app(device, csv_path=path)
    try:
        initial = refresh_dashboard(app, action="loop")
        assert device.identity is None and device.transport._serial is None
        assert initial["poll"]["disabled"] and not initial["connect"]["disabled"]
        assert all(initial[key]["disabled"] for key in ("set", "loop", "pump"))
        assert not path.exists()
        connected = refresh_dashboard(app, changed=["connect.n_clicks", "loop.n_clicks"])
        assert not connected["poll"]["disabled"] and connected["connect"]["disabled"]
        assert "SIMULATOR-0001" in connected["identity"]["children"]
        assert connected["target"]["value"] == 20
        assert not any(b"," in frame for frame in device.transport._serial.commands)
        assert app.nrc_session.recorder.rows == 1
    finally:
        app.nrc_session.shutdown()
    with path.open(newline="") as stream:
        assert next(csv.DictReader(stream))["serial_number"] == "SIMULATOR-0001"


def test_shutdown_closes_csv_and_port_without_control_writes(tmp_path):
    device = Chiller(Simulator())
    app = create_app(device, csv_path=tmp_path / "shutdown.csv")
    refresh_dashboard(app, action="connect")
    refresh_dashboard(app, action="loop")
    serial = device.transport._serial
    before = list(serial.commands)
    stopped = refresh_dashboard(
        app, changed=["poll.n_intervals", "shutdown.n_clicks", "loop.n_clicks"]
    )
    assert app.nrc_session.closed and device.identity is None
    assert device.transport._serial is None and serial.commands == before
    assert app.nrc_session.recorder._file.closed
    assert stopped["poll"]["disabled"] and stopped["connect"]["disabled"]
    assert stopped["shutdown"]["disabled"]
    assert all(stopped[key]["disabled"] for key in ("target", "loop", "pump"))
    refresh_dashboard(app, action="connect")
    refresh_dashboard(app, action="loop")
    assert device.identity is None and serial.commands == before


def test_shutdown_before_connect_does_not_open_device_or_csv(tmp_path):
    device = Chiller(Simulator())
    path = tmp_path / "unused.csv"
    app = create_app(device, csv_path=path)
    result = refresh_dashboard(app, action="shutdown")
    assert result["shutdown"]["disabled"]
    assert device.transport._serial is None and not path.exists()


def test_cli_gui_defers_connect_until_operator_request(monkeypatch):
    import tark_nrc.gui as gui

    def serve(device, **kwargs):
        assert device.identity is None and device.transport._serial is None
        assert kwargs["csv_path"] == "outputs/deferred.csv"

    monkeypatch.setattr(gui, "run_dashboard", serve)
    assert main(["--simulate", "gui", "--csv", "outputs/deferred.csv"]) == 0


@pytest.mark.parametrize(
    "filename,args", [("config.toml", ["gui"]), ("other.toml", ["--config", "other.toml", "gui"])]
)
def test_cli_gui_loads_local_config_without_opening_port(tmp_path, monkeypatch, filename, args):
    import tark_nrc.gui as gui

    monkeypatch.chdir(tmp_path)
    (tmp_path / filename).write_text(
        '[serial]\nport="COM9999"\n[control]\nexpected_serial_number="TEST-SERIAL"\n'
    )
    seen = []

    def serve(device, **kwargs):
        seen.append(device)
        assert device.transport.backend == "hardware"
        assert device.transport.settings.port == "COM9999"
        assert device.identity is None and device.transport._serial is None

    monkeypatch.setattr(gui, "run_dashboard", serve)
    assert main(args) == 0
    assert len(seen) == 1


def test_cli_gui_missing_default_config_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["gui"]) == 1
    assert "config.toml" in capsys.readouterr().out


def test_gui_shutdown_returns_http_response_and_stops_real_server(monkeypatch):
    from threading import Event, Thread
    from urllib.request import Request, urlopen

    import werkzeug.serving

    import tark_nrc.gui as gui

    ready = Event()
    captured = {}
    make_server = werkzeug.serving.make_server
    create = gui.create_app

    def capture_app(*args, **kwargs):
        app = create(*args, **kwargs)
        captured["app"] = app
        return app

    def capture_server(*args, **kwargs):
        server = make_server(*args, **kwargs)
        captured["server"] = server
        ready.set()
        return server

    monkeypatch.setattr(gui, "create_app", capture_app)
    monkeypatch.setattr(werkzeug.serving, "make_server", capture_server)
    device = Chiller(Simulator())
    errors = []

    def serve():
        try:
            gui.run_dashboard(device, port=0)
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert ready.wait(5), errors
        url = f"http://127.0.0.1:{captured['server'].server_port}"
        with urlopen(url, timeout=5) as response:
            assert response.status == 200
        for action in ("connect", "shutdown"):
            payload = dashboard_payload(captured["app"], action=action)
            request = Request(
                url + "/_dash-update-component",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                result = json.load(response)["response"]
            if action == "connect":
                assert not result["poll"]["disabled"]
            else:
                assert "GUI shut down" in result["notice"]["children"]
        thread.join(5)
        assert not thread.is_alive() and not errors
        assert device.identity is None and captured["app"].nrc_session.closed
    finally:
        if thread.is_alive() and "server" in captured:
            Thread(target=captured["server"].shutdown, daemon=True).start()
            thread.join(5)


def test_cli_controls_work_without_permission_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["read"])
    assert exc.value.code == 2
    assert main(["--simulate", "set", "45"]) == 0
    assert "45.00 C" in capsys.readouterr().out


@pytest.mark.parametrize(
    "button,field,expected",
    [("set", "setpoint_c", 45), ("loop", "control_loop", "R"), ("pump", "pump", "ON")],
)
def test_each_combined_control_works_from_quit(button, field, expected):
    with Chiller(Simulator()) as device:
        peer = device.transport._serial
        peer.loop = "QUIT"
        app = create_app(device)
        initial = refresh_dashboard(app)
        assert initial["loop"]["children"] == "Run loop"
        assert initial["pump"]["children"] == "Pump on"
        assert all(not initial[key]["disabled"] for key in ("target", "set", "loop", "pump"))
        before = len(peer.commands)
        result = refresh_dashboard(app, target=45, action=button)
        assert getattr(app.nrc_session.history[-1], field) == expected
        assert sum(b"," in frame for frame in peer.commands[before:]) == 1
        assert not app.nrc_session.failed and not result["poll"]["disabled"]
        if button in ("loop", "pump"):
            before = len(peer.commands)
            result = refresh_dashboard(app, action=button)
            assert getattr(app.nrc_session.history[-1], field) == (
                "S" if button == "loop" else "OFF"
            )
            assert sum(b"," in frame for frame in peer.commands[before:]) == 1


@pytest.mark.parametrize("latest_loop", ["QUIT", "R"])
def test_stop_accepts_persistent_quit_but_rejects_running_sample(monkeypatch, latest_loop):
    with Chiller(Simulator()) as device:
        peer = device.transport._serial
        peer.loop = "QUIT"
        write = peer.write

        def reply_with_quit(frame):
            count = write(frame)
            if frame == b"$PCCTL,S\r\n":
                peer.loop = "QUIT"  # Operator-observed terminal reply on firmware 1.0.21.
            return count

        monkeypatch.setattr(peer, "write", reply_with_quit)
        read_sample = device.read_sample
        monkeypatch.setattr(
            device, "read_sample", lambda: replace(read_sample(), control_loop=latest_loop)
        )
        app = create_app(device)
        app._browser_button_labels = {"loop": "Stop loop", "pump": "Pump on"}
        result = refresh_dashboard(app, action="loop")
        assert peer.commands.count(b"$PCCTL,S\r\n") == 1
        assert app.nrc_session.failed == (latest_loop == "R")
        if latest_loop == "QUIT":
            assert "QUIT / OFF" in json.dumps(result["telemetry"])
            assert "Stop readback accepted" in result["notice"]["children"]
        else:
            assert "latest control_loop" in result["notice"]["children"]


def test_all_controls_remain_available_while_running_and_after_device_rejection():
    with Chiller(Simulator()) as device:
        device.run()
        app = create_app(device)
        state = refresh_dashboard(app)
        assert all(not state[key]["disabled"] for key in ("target", "set", "loop", "pump"))
        rejected = refresh_dashboard(app, action="pump")
        assert "device rejected" in rejected["notice"]["children"]
        assert not rejected["loop"]["disabled"] and not rejected["poll"]["disabled"]
        assert device.transport._serial.commands.count(b"$PCPMP,OFF\r\n") == 1
        stopped = refresh_dashboard(app, action="loop")
        assert not app.nrc_session.failed
        assert "S / ON" in json.dumps(stopped["telemetry"])


def test_readback_drift_reports_actual_sample_without_locking_next_action(monkeypatch):
    with Chiller(Simulator()) as device:
        app = create_app(device)
        original = device.read_sample
        monkeypatch.setattr(device, "read_sample", lambda: replace(original(), control_loop="S"))
        failed = refresh_dashboard(app, action="loop")
        assert "latest control_loop" in failed["notice"]["children"]
        assert "S / ON" in json.dumps(failed["telemetry"])
        assert not failed["loop"]["disabled"] and not failed["poll"]["disabled"]
        assert device.transport._serial.commands.count(b"$PCCTL,R\r\n") == 1
        monkeypatch.setattr(device, "read_sample", original)
        refresh_dashboard(app, target=21, action="set")
        assert not app.nrc_session.failed


def test_csv_error_preserves_readings_and_does_not_block_later_control():
    class BrokenRecorder:
        path = "broken.csv"

        def record(self, *args, **kwargs):
            raise PermissionError("Disk full")

    with Chiller(Simulator()) as device:
        app = create_app(device, recorder=BrokenRecorder())
        reading = refresh_dashboard(app)
        assert "Disk full" in reading["notice"]["children"]
        assert "24.00" in json.dumps(reading["telemetry"])
        assert not app.nrc_session.failed and not reading["loop"]["disabled"]
        running = refresh_dashboard(app, action="loop")
        assert "R / ON" in json.dumps(running["telemetry"])
        assert "CSV recording stopped" in running["notice"]["children"]
        assert not running["poll"]["disabled"]


def test_link_failure_offers_explicit_reconnect_without_restart_or_replay(monkeypatch):
    device = Chiller(Simulator())
    app = create_app(device)
    connect = device.connect
    read = device.read_sample

    def fail():
        raise CommunicationError("Link failed")

    monkeypatch.setattr(device, "connect", fail)
    rejected = refresh_dashboard(app, action="connect")
    assert not rejected["connect"]["disabled"] and rejected["poll"]["disabled"]
    monkeypatch.setattr(device, "connect", connect)
    try:
        refresh_dashboard(app, action="connect")
        peer = device.transport._serial
        monkeypatch.setattr(device, "read_sample", fail)
        failed = refresh_dashboard(app)
        assert device.identity is None and not peer.is_open
        assert not failed["connect"]["disabled"] and failed["poll"]["disabled"]
        monkeypatch.setattr(device, "read_sample", read)
        reconnected = refresh_dashboard(app, action="connect")
        assert not app.nrc_session.failed and not reconnected["loop"]["disabled"]
        assert not any(b"," in frame for frame in device.transport._serial.commands)
        refresh_dashboard(app, action="loop")
        assert device.transport._serial.commands.count(b"$PCCTL,R\r\n") == 1
    finally:
        app.nrc_session.shutdown()


def test_explicit_reconnect_still_verifies_original_device_identity(monkeypatch):
    with Chiller(Simulator()) as device:
        session = DashboardSession(device)
        session.failed = True
        original = device.connect

        def replacement():
            original()
            device.identity = replace(device.identity, serial_number="OTHER-UNIT")

        monkeypatch.setattr(device, "connect", replacement)
        sample, message = session.connect()
        assert sample is None and "Connection unit changed" in message
        assert device.identity is None


def test_polling_follows_panel_state_without_sending_commands():
    with Chiller(Simulator()) as device:
        app = create_app(device)
        peer = device.transport._serial
        first = refresh_dashboard(app)
        assert first["loop"]["children"] == "Run loop"
        assert first["pump"]["children"] == "Pump on"
        peer.loop, peer.pump = "R", "ON"
        running = refresh_dashboard(app)
        assert running["loop"]["children"] == "Stop loop"
        assert running["pump"]["children"] == "Pump off"
        peer.loop, peer.pump = "S", "OFF"
        stopped = refresh_dashboard(app)
        assert stopped["loop"]["children"] == "Run loop"
        assert stopped["pump"]["children"] == "Pump on"
        assert not any(b"," in frame for frame in peer.commands)


@pytest.mark.parametrize("button", ["loop", "pump"])
def test_panel_change_before_click_does_not_reverse_the_displayed_request(button):
    with Chiller(Simulator()) as device:
        peer = device.transport._serial
        if button == "loop":
            device.run()
        else:
            device.set_pump(True)
        app = create_app(device)
        displayed = refresh_dashboard(app)
        assert displayed[button]["children"] == ("Stop loop" if button == "loop" else "Pump off")
        # Panel changes after the browser rendered its button, before the click arrives.
        peer.loop, peer.pump = "S", "OFF"
        before = len(peer.commands)
        result = refresh_dashboard(app, action=button)
        writes = [frame for frame in peer.commands[before:] if b"," in frame]
        assert writes == [b"$PCCTL,S\r\n" if button == "loop" else b"$PCPMP,OFF\r\n"]
        assert result[button]["children"] == ("Run loop" if button == "loop" else "Pump on")


def test_unconfirmed_run_still_offers_explicit_stop_without_claiming_running(monkeypatch):
    with Chiller(Simulator()) as device:
        peer = device.transport._serial
        peer.loop = "QUIT"
        request = device.transport.request

        def unconfirmed(command, data=None):
            result = request(command, data)
            if command == "CTL" and data == "R":
                peer.loop = "QUIT"
            return result

        monkeypatch.setattr(device.transport, "request", unconfirmed)
        app = create_app(device)
        refresh_dashboard(app)
        failed = refresh_dashboard(app, action="loop")
        assert "QUIT" in failed["notice"]["children"]
        assert failed["loop"]["children"] == "Stop loop"
        polled = refresh_dashboard(app)
        assert "QUIT / ON" in json.dumps(polled["telemetry"])
        assert polled["loop"]["children"] == "Stop loop"
        stopped = refresh_dashboard(app, action="loop")
        assert stopped["loop"]["children"] == "Run loop"
        assert peer.commands.count(b"$PCCTL,R\r\n") == 1
        assert peer.commands.count(b"$PCCTL,S\r\n") == 1
