"""Local dashboard; one synchronous session owns the controller and its CSV."""

from collections import deque
from threading import RLock

from .controller import Chiller, finite
from .transport import ProtocolError

CONTROLS = {
    "set": "Apply setpoint",
    "run": "Run",
    "stop": "Stop loop",
    "pump-on": "Pump on",
    "pump-off": "Pump off",
}


class DashboardSession:
    """Serialize actions, fresh readback, CSV recording and display history.

    A rejected input still refreshes telemetry. A communication/readback/CSV fault
    locks polling and activation; explicit deactivation can reconnect for recovery.
    """

    def __init__(self, chiller: Chiller, recorder=None):
        self.chiller = chiller
        self._identity = chiller.identity
        self.recorder = recorder
        self.history = deque(maxlen=600)
        self.failed = False
        self.message = "Reading the chiller…"
        self.applied_action = None
        self._lock = RLock()

    def refresh(self, action=None, target_c=None):
        """Perform at most one write action and return a fresh sample plus its notice."""
        with self._lock:
            self.applied_action = None
            recovering = self.failed
            if recovering and (not self.chiller.allow_writes or action not in ("stop", "pump-off")):
                return None, self.message
            expected = None
            rejected = False
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
            try:
                sample = self.chiller.read_sample()
                if expected:
                    field, value = expected
                    actual = getattr(sample, field)
                    matches = (
                        abs(actual - value) <= 0.001 if field == "setpoint_c" else actual == value
                    )
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
        """Use only verified controller methods; name the field the final sample must match."""
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
        """Latch the fault; expose operator recovery without replaying any action."""
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
    """Compute availability from current readback; faults permit deactivation only."""
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


def create_app(chiller: Chiller, *, recorder=None, interval_s=1.0):
    """Build a local dashboard for an already connected, exclusively owned controller."""
    from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

    interval = finite(interval_s, "Poll interval")
    if interval <= 0:
        raise ValueError("Poll interval must be positive")
    if chiller.identity is None:
        raise RuntimeError("Connect before creating the dashboard")
    session = DashboardSession(chiller, recorder)
    app = Dash(__name__, title="NRC400 · Thermal control")
    app.nrc_session = session
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
                f"{identity.model} · {identity.serial_number} · {identity.firmware}",
                className="identity",
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
                    html.Span(f"CSV: {recorder.path}" if recorder else "CSV recording off"),
                    html.Span("Level status only. Read other alarms on the chiller’s front panel."),
                ]
            ),
            dcc.Interval(id="poll", interval=max(1, round(interval * 1000))),
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
        + [Output(key, "disabled") for key in CONTROLS],
        [Input("poll", "n_intervals"), *[Input(key, "n_clicks") for key in CONTROLS]],
        [State("target", "value"), State("reported-target", "data")],
    )
    def update(_tick, _set, _run, _stop, _on, _off, target, previous_target):
        """One callback owns control actions and all display updates from their readback."""
        # Dash can batch a timer tick and click into one request. Do not let the
        # first trigger (often poll) swallow the operator's explicit command.
        triggered = set(ctx.triggered_prop_ids.values()).intersection(CONTROLS)
        deactivation = next((key for key in ("stop", "pump-off") if key in triggered), None)
        action = deactivation or (next(iter(triggered)) if len(triggered) == 1 else None)
        sample, message = session.refresh(action, target)
        if len(triggered) > 1 and not session.failed:
            message = (
                f"{message} {deactivation} selected from simultaneous controls."
                if deactivation
                else "Multiple controls received; choose one action at a time."
            )
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
        disabled = disabled_controls(sample, writable=writable, failed=session.failed)
        return (
            metrics,
            history_figure(session.history),
            message,
            session.failed,
            reported_target,
            blocked,
            sample.setpoint_c if sample else no_update,
            *[disabled[key] for key in CONTROLS],
        )

    return app
