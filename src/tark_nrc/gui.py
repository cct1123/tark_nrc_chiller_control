"""User interfaces for the controller: a local Dash dashboard and a command line.

The CLI starts at main(), near the bottom of this file. The dashboard starts at
create_app(): it builds the page and one callback. That callback uses
DashboardSession to run commands, read fresh state, record CSV and keep history.
Dash/Plotly are imported only when needed, so CLI reads do not load the GUI.
"""

import argparse
import json
import logging
import math
import time
from collections import deque
from dataclasses import asdict
from threading import RLock, Thread

from .controller import COMMANDS, Chiller, CsvRecorder, ProtocolError, create_chiller, finite_number

# Keep this order aligned with update()'s button arguments and callback outputs.
CONTROLS = {
    "set": "Apply setpoint",
    "run": "Run",
    "stop": "Stop loop",
    "pump-on": "Pump on",
    "pump-off": "Pump off",
}


class DashboardSession:
    """Own the dashboard's connection, optional CSV, history and failure state.

    connect() opens the device only on request. refresh() handles one optional
    action and returns (sample, message) for the page; sample=None means there is
    no current reading. shutdown() closes resources without stopping equipment.

    failed stays True after a communication/readback/CSV fault. Only an explicit
    Stop/Pump off may reconnect for deactivation; other actions need an app restart.
    closed means the GUI has shut down and no further connection/action is allowed.
    """

    def __init__(self, chiller: Chiller, recorder=None, *, csv_path=None):
        self.chiller = chiller
        self._identity = chiller.identity
        self.recorder = recorder
        self.csv_path = csv_path
        self.history = deque(maxlen=600)
        self.failed = False
        self.closed = False
        self.message = (
            "Reading the chiller…" if chiller.identity else "Disconnected. Press Connect."
        )
        self.applied_action = None
        self._lock = RLock()

    def connect(self):
        """Connect on operator request using queries only; faults still require restart."""
        with self._lock:
            if self.closed or self.failed:
                return None, self.message
            if self.chiller.identity is not None:
                return self.refresh()
            try:
                self.chiller.connect()
                if self._identity and (
                    self.chiller.identity.serial_number != self._identity.serial_number
                ):
                    raise ProtocolError("Connection unit changed; no control command was sent")
                self._identity = self.chiller.identity
                if self.csv_path and self.recorder is None:
                    self.recorder = CsvRecorder(
                        self.csv_path, self._identity, self.chiller.transport.backend
                    )
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                self.chiller.close()
                self.message = (
                    f"Connection failed: {exc}. Check the device and press Connect to retry."
                )
                return None, self.message
            return self.refresh()

    def shutdown(self):
        """Release resources once without sending Stop or pump commands."""
        with self._lock:
            if self.closed:
                return None, self.message
            self.closed = True
            self.applied_action = None
            errors = []
            for resource in (self.chiller, self.recorder):
                if resource is not None:
                    try:
                        resource.close()
                    except OSError as exc:
                        errors.append(str(exc))
            self.message = "GUI shut down. The chiller retains its loop and pump state."
            if errors:
                self.message += f" Resource cleanup error: {'; '.join(errors)}"
            return None, self.message

    def refresh(self, action=None, target_c=None):
        """Apply one action, read current state, then record it; return (sample, message).

        Input errors still allow a fresh reading. Device/recording failures lock
        the session. A successful action must also agree with the final full sample,
        since the panel could have changed after the method's own readback.
        """
        with self._lock:
            self.applied_action = None
            if self.closed or (self.chiller.identity is None and not self.failed):
                return None, self.message
            recovering = self.failed
            if recovering and (not self.chiller.allow_writes or action not in ("stop", "pump-off")):
                return None, self.message
            expected = None
            rejected = False
            # First perform the requested action, if any. Never replay a failed action.
            try:
                if recovering:
                    # Explicit operator deactivation only: reconnect/reidentify,
                    # send once, verify. Never replay the failed prior action.
                    self.chiller.close()
                    self.chiller.connect()
                    # A COM port can be reassigned. Recovery must remain on the
                    # original unit even when no TOML serial binding was supplied.
                    if self.chiller.identity.serial_number != self._identity.serial_number:
                        self.chiller.close()
                        raise ProtocolError("Recovery unit changed; no control command was sent")
                if action is not None:
                    expected = self._apply_action(action, target_c)
            except (ValueError, TypeError, PermissionError) as exc:
                # The rejection is not a sample: query fresh state separately so
                # invalid input cannot hide a running loop or disable Stop.
                self.message = str(exc)
                rejected = True
            except (OSError, RuntimeError) as exc:
                return self._fail(exc)
            # Always read again, even after rejected input, so Stop can remain available.
            try:
                sample = self.chiller.read_sample()
                if expected:
                    field, value = expected
                    actual = getattr(sample, field)
                    if field == "setpoint_c":
                        matches = abs(actual - value) <= 0.001
                    else:
                        matches = actual == value
                    if not matches:
                        raise ProtocolError(
                            f"{action}: verified {value!r}, but latest {field} is {actual!r}; "
                            "inspect the panel; write was not retried"
                        )
                    self.applied_action = action
                self.history.append(sample)
                if action is None and not rejected:
                    self.message = f"Updated {sample.timestamp_utc}"
                elif recovering and not rejected:
                    self.message = (
                        f"Operator recovery verified: loop {sample.control_loop}, "
                        f"pump {sample.pump}. Session remains locked; "
                        "restart before new control actions."
                    )
            except (OSError, RuntimeError) as exc:
                return self._fail(exc)
            if self.recorder:
                try:
                    self.recorder.record(sample)
                except OSError as exc:
                    # Preserve verified device state while locking the session
                    # because evidence recording failed. Do not repeat the write.
                    return self._fail(exc, sample)
            return sample, self.message

    def _apply_action(self, action, target_c):
        """Call the controller and return the expected sample field/value.

        For example, Run returns ("control_loop", "R"). refresh() compares that
        with the next full sample before reporting success to the user.
        """
        if action == "set":
            reported = self.chiller.set_setpoint(target_c)
            self.message = f"Setpoint readback: {reported:.2f} °C"
            return "setpoint_c", reported
        if action in ("run", "stop"):
            if action == "run":
                self.chiller.run()
            else:
                self.chiller.stop()
            expected = "R" if action == "run" else "S"
            self.message = f"Control loop readback: {expected}"
            return "control_loop", expected
        if action in ("pump-on", "pump-off"):
            self.chiller.set_pump(action == "pump-on")
            expected = "ON" if action == "pump-on" else "OFF"
            self.message = f"Pump readback: {expected}"
            return "pump", expected
        raise ValueError(f"Unknown dashboard action: {action}")

    def _fail(self, exc, sample=None):
        """Lock the session and return a notice, keeping a valid sample if CSV alone failed."""
        self.failed = True
        recovery = (
            "Operator Stop loop/Pump off can reconnect for verified deactivation. "
            if self.chiller.allow_writes
            else "Use the front panel for physical control. "
        )
        self.message = (
            f"Session stopped: {exc}. New control actions are locked. "
            f"{recovery}Restart the app before other actions."
        )
        if self.recorder:
            try:
                self.recorder.record(error=self.message)
            except OSError as log_exc:
                self.message += f" CSV failed: {log_exc}"
        return sample, self.message


def disabled_controls(sample, *, writable, failed):
    """Return {button_id: disabled}; True means the button cannot be clicked.

    Use current device state, not previous clicks. During a failure only Stop and
    Pump off remain available for explicit recovery, and only if writes are enabled.
    """
    disabled = dict.fromkeys(CONTROLS, True)
    if not writable:
        return disabled
    if failed:
        disabled.update({"stop": False, "pump-off": False})
        return disabled
    if sample is not None:
        stopped = sample.control_loop == "S"
        level_ok = sample.level == "OK"
        disabled.update(
            {
                "set": False,
                "run": not stopped or not level_ok,
                "stop": stopped,
                "pump-on": not stopped or sample.pump == "ON" or not level_ok,
                "pump-off": not stopped or sample.pump == "OFF",
            }
        )
    return disabled


def history_figure(history):
    """Plot only successful device readbacks retained in the bounded history."""
    import plotly.graph_objects as go

    samples = tuple(history)
    figure = go.Figure()
    for label, field, color, dash in [
        ("Supply", "temperature_c", "#007d85", "solid"),
        ("Setpoint", "setpoint_c", "#c28a32", "dash"),
    ]:
        figure.add_scatter(
            x=[sample.timestamp_utc for sample in samples],
            y=[getattr(sample, field) for sample in samples],
            name=label,
            mode="lines",
            line={"color": color, "width": 2, "dash": dash},
        )
    figure.update_layout(
        template="plotly_white",
        height=320,
        margin={"l": 55, "r": 20, "t": 15, "b": 45},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis_title="°C",
        xaxis_title="UTC",
        legend={"orientation": "h", "y": 1.12},
        uirevision="session",
    )
    return figure


def create_app(chiller: Chiller, *, recorder=None, csv_path=None, interval_s=1.0):
    """Build a Dash app without starting a web server or opening an unopened device.

    run_dashboard() serves the app. The Connect button starts device access and
    creates csv_path, if supplied. For an already connected chiller, an existing
    recorder may be supplied instead. The app's DashboardSession owns cleanup.
    """
    from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

    interval = finite_number(interval_s, "Poll interval")
    if interval <= 0:
        raise ValueError("Poll interval must be positive")
    if recorder is not None and csv_path is not None:
        raise ValueError("Select a recorder or CSV path, not both")
    if recorder is not None and chiller.identity is None:
        raise ValueError("An existing recorder requires a connected controller")
    session = DashboardSession(chiller, recorder, csv_path=csv_path)
    app = Dash(__name__, title="NRC400 · Thermal control")
    # Expose the owned session for embedding/tests. run_dashboard installs shutdown.
    app.nrc_session = session
    app.nrc_shutdown = None
    writable = chiller.allow_writes
    identity = chiller.identity

    def panel(title, caption, *children):
        return html.Section(
            [
                html.Div(
                    [html.H2(title), html.Span(caption, className="muted")],
                    className="section-title",
                ),
                *children,
            ],
            className="panel",
        )

    app.layout = html.Main(
        [
            html.Header(
                [
                    html.Div(
                        [
                            html.P("LABORATORY / THERMAL SYSTEMS", className="eyebrow"),
                            html.H1("NRC400"),
                            html.P("Recirculating chiller", className="subtitle"),
                        ]
                    ),
                    html.Div(chiller.transport.backend.upper(), className="backend"),
                ]
            ),
            html.Div(
                f"{identity.model} · {identity.serial_number} · {identity.firmware}"
                f" · {chiller.transport.port}"
                if identity
                else "Disconnected",
                id="identity",
                className="identity",
            ),
            html.Div(
                [
                    html.Button("Connect", id="connect", disabled=identity is not None),
                    html.Button("Shut down GUI", id="shutdown"),
                    html.Span(
                        "Shutting down the GUI leaves the loop and pump unchanged.",
                        className="muted",
                    ),
                ],
                className="controls",
            ),
            html.Div(id="telemetry", className="metrics"),
            panel(
                "Temperature history",
                "Last 600 samples · Celsius",
                dcc.Graph(id="trend", config={"displayModeBar": False}),
            ),
            panel(
                "Control",
                "Writes enabled" if writable else "Read-only session",
                html.Div(
                    [
                        html.Label("Setpoint / °C", htmlFor="target"),
                        dcc.Input(
                            id="target",
                            type="number",
                            value=None,
                            step=0.1,
                            min=chiller.min_c,
                            max=chiller.max_c,
                            disabled=True,
                        ),
                        *[
                            html.Button(
                                label,
                                id=key,
                                disabled=True,
                                **({"className": "primary"} if key == "run" else {}),
                            )
                            for key, label in CONTROLS.items()
                        ],
                    ],
                    className="controls",
                ),
                html.P(
                    f"Setpoint window {chiller.min_c:g}–{chiller.max_c:g} °C. "
                    "Stop the loop before using pump-only controls.",
                    className="muted",
                ),
                html.Div(id="notice", role="status"),
            ),
            html.Footer(
                [
                    html.Span(
                        f"CSV: {recorder.path if recorder else csv_path}"
                        if recorder or csv_path
                        else "CSV recording off"
                    ),
                    html.Span("Level status only. Read other alarms on the chiller’s front panel."),
                ]
            ),
            dcc.Interval(
                id="poll", interval=max(1, round(interval * 1000)), disabled=identity is None
            ),
            # Per-browser readback lets polling follow panel changes without replacing
            # an operator's draft target. This is display state, never a device cache.
            dcc.Store(id="reported-target"),
        ]
    )

    @app.callback(
        [
            Output("telemetry", "children"),
            Output("trend", "figure"),
            Output("notice", "children"),
            Output("poll", "disabled"),
            Output("target", "value"),
            Output("target", "disabled"),
            Output("reported-target", "data"),
        ]
        + [Output(key, "disabled") for key in CONTROLS]
        + [
            Output("identity", "children"),
            Output("connect", "disabled"),
            Output("shutdown", "disabled"),
        ],
        [
            Input("poll", "n_intervals"),
            *[Input(key, "n_clicks") for key in CONTROLS],
            Input("connect", "n_clicks"),
            Input("shutdown", "n_clicks"),
        ],
        [State("target", "value"), State("reported-target", "data")],
    )
    def update(_tick, _set, _run, _stop, _on, _off, _connect, _shutdown, target, previous_target):
        """Choose an action, refresh the session, and return values in Output order.

        The click counts only trigger this callback; they are not device state.
        ctx tells us which inputs changed in this request, including batched events.
        """
        # Dash can batch a timer tick and click into one request. Do not let the
        # first trigger (often poll) swallow the operator's explicit command.
        events = set(ctx.triggered_prop_ids.values())
        triggered = events.intersection(CONTROLS)
        deactivation = None
        if "stop" in triggered:
            deactivation = "stop"
        elif "pump-off" in triggered:
            deactivation = "pump-off"

        action = None
        if deactivation is not None:
            action = deactivation
        elif len(triggered) == 1:
            action = next(iter(triggered))  # The only control in this request.
        if "shutdown" in events:
            sample, message = session.shutdown()
            if app.nrc_shutdown:
                app.nrc_shutdown()
        elif "connect" in events:
            sample, message = session.connect()
        else:
            sample, message = session.refresh(action, target)
        if len(triggered) > 1 and not session.failed and not session.closed:
            if deactivation is not None:
                message = f"{message} {deactivation} selected from simultaneous controls."
            else:
                message = "Multiple controls received; choose one action at a time."
        values = [
            ("SUPPLY TEMPERATURE", f"{sample.temperature_c:.2f} °C" if sample else "—"),
            ("REPORTED SETPOINT", f"{sample.setpoint_c:.2f} °C" if sample else "—"),
            ("REPORTED LOOP / PUMP", f"{sample.control_loop} / {sample.pump}" if sample else "—"),
            ("COOLANT LEVEL", sample.level if sample else "—"),
        ]
        metrics = [
            html.Div([html.P(label), html.Strong(value)], className="metric")
            for label, value in values
        ]
        synchronize_target = target == previous_target or session.applied_action == "set"
        reported_target = sample.setpoint_c if sample and synchronize_target else no_update
        blocked = not writable or session.failed or sample is None
        disabled = disabled_controls(
            sample, writable=writable and not session.closed, failed=session.failed
        )
        identity = chiller.identity
        return (
            metrics,
            history_figure(session.history),
            message,
            session.failed or session.closed or identity is None,
            reported_target,
            blocked,
            sample.setpoint_c if sample else no_update,
            *[disabled[key] for key in CONTROLS],
            f"{identity.model} · {identity.serial_number} · {identity.firmware}"
            f" · {chiller.transport.port}"
            if identity
            else "Disconnected",
            identity is not None or session.failed or session.closed,
            session.closed,
        )

    return app


def run_dashboard(chiller: Chiller, *, recorder=None, csv_path=None, interval_s=1.0, port=8057):
    """Serve locally until GUI shutdown or Ctrl+C, then release all session resources."""
    from werkzeug.serving import make_server

    app = create_app(chiller, recorder=recorder, csv_path=csv_path, interval_s=interval_s)
    try:
        with make_server("127.0.0.1", port, app.server, threaded=False) as server:
            # shutdown() must run outside the serving thread. It waits until the
            # current callback response is delivered before ending serve_forever().
            def shutdown_server():
                Thread(target=server.shutdown, daemon=True).start()

            app.nrc_shutdown = shutdown_server
            print(f"{chiller.transport.backend}: http://127.0.0.1:{server.server_port}")
            server.serve_forever()
    finally:
        app.nrc_session.shutdown()


def positive(value: str) -> float:
    """Validate a CLI duration/interval; argparse displays the error to the user."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Must be a positive finite number")
    return result


def build_parser():
    """Declare CLI options; parsing them does not connect to the chiller."""
    parser = argparse.ArgumentParser(description="NRC400 RS-232 controller; API temperatures in C")
    parser.add_argument("--config", help="TOML configuration (GUI defaults to config.toml)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--simulate", action="store_true", help="Memory-only serial simulator")
    source.add_argument("--port", help="Verified OS serial port, e.g. COM3")
    parser.add_argument(
        "--allow-writes", action="store_true", help="Enable explicit control actions"
    )
    parser.add_argument("--verbose", action="store_true", help="Log serial TX/RX")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("ports", help="List OS serial ports without opening them")
    commands.add_parser("identify", help="Read model, serial number and firmware")
    commands.add_parser("read", help="Read temperatures and operating state")
    query = commands.add_parser("query", help="Read one documented command")
    query.add_argument("code", choices=sorted(COMMANDS))
    setpoint = commands.add_parser("set", help="Set temperature in Celsius and verify readback")
    setpoint.add_argument("target_c", type=float)
    commands.add_parser("run", help="Start the control loop")
    commands.add_parser("stop", help="Stop the control loop")
    pump = commands.add_parser("pump", help="Pump-only control, with the loop stopped")
    pump.add_argument("state", choices=["on", "off"])
    units = commands.add_parser("units", help="Change the front-panel temperature units")
    units.add_argument("value", choices=["C", "F"])
    clock = commands.add_parser("time", help="Set device time as HH:MM:SS")
    clock.add_argument("value")
    log = commands.add_parser("log", help="Record samples to a new CSV")
    log.add_argument("--csv", required=True)
    log.add_argument("--duration", type=positive, default=10.0)
    gui = commands.add_parser("gui", help="Open a localhost Dash dashboard")
    gui.add_argument("--csv", help="Optional new CSV to record every displayed sample")
    for child in (log, gui):
        child.add_argument("--interval", type=positive, default=1.0)
    gui.add_argument("--http-port", type=int, default=8057)
    return parser


def log_samples(chiller, recorder, *, duration_s, interval_s):
    """Record sequential samples until the deadline; record a fault once and stop."""
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            sample = chiller.read_sample()
        except (OSError, RuntimeError) as exc:
            recorder.record(error=str(exc))
            raise
        recorder.record(sample)
        print(f"{sample.temperature_c:.2f} C | target {sample.setpoint_c:.2f} C")
        # Account for serial time; never sleep past the requested recording end.
        remaining_interval = interval_s - (time.monotonic() - started)
        remaining_duration = deadline - time.monotonic()
        time.sleep(max(0, min(remaining_interval, remaining_duration)))
    print(f"Saved {recorder.rows} rows to {recorder.path}")


def execute_command(chiller, args):
    """Dispatch through verified API methods and close the optional CSV on exit."""
    command = args.command
    if command == "gui":
        run_dashboard(chiller, csv_path=args.csv, interval_s=args.interval, port=args.http_port)
        return
    if command == "identify":
        print(json.dumps(asdict(chiller.identity), indent=2))
    elif command == "query":
        print(chiller.query(args.code))
    elif command == "set":
        print(f"Reported setpoint: {chiller.set_setpoint(args.target_c):.2f} C")
    elif command == "time":
        print(f"Device time: {chiller.set_time(args.value)}")
    elif command in ("read", "run", "stop", "pump", "units"):
        if command == "run":
            chiller.run()
        elif command == "stop":
            chiller.stop()
        elif command == "pump":
            chiller.set_pump(args.state == "on")
        elif command == "units":
            chiller.set_units(args.value)
        print(json.dumps(chiller.read_sample().to_dict(), indent=2))
    elif command == "log":
        with CsvRecorder(args.csv, chiller.identity, chiller.transport.backend) as recorder:
            log_samples(chiller, recorder, duration_s=args.duration, interval_s=args.interval)


def main(argv=None):
    """Run one CLI invocation and return an exit code (0 success, 1 runtime error).

    Non-GUI commands connect immediately. The GUI waits for its Connect button.
    Either path closes the connection on completion, failure or Ctrl+C.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.command == "ports":
        from serial.tools.list_ports import comports

        for port in comports():
            print(
                f"{port.device}: {port.description} | USB serial: {port.serial_number or 'unknown'}"
            )
        return 0
    if not args.simulate and args.port is None and args.config is None:
        if args.command == "gui":
            args.config = "config.toml"
        else:
            parser.error("Select --simulate, --port or --config explicitly")
    try:
        chiller = create_chiller(
            args.config,
            simulate=args.simulate,
            port=args.port,
            allow_writes=args.allow_writes,
        )
        try:
            if args.command != "gui":
                chiller.connect()
            execute_command(chiller, args)
        finally:
            chiller.close()
        return 0
    except KeyboardInterrupt:
        print("Session closed. The chiller retains its state; stop explicitly if required.")
        return 0
    except (OSError, RuntimeError, ValueError, TypeError, ImportError) as exc:
        print(f"Error: {exc}")
        return 1
