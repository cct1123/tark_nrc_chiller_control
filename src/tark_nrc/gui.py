"""Local dashboard; one synchronous session owns the controller and its CSV."""

from collections import deque
from threading import RLock

from .controller import Chiller, finite

CONTROLS = {"set": "Apply setpoint", "run": "Run", "stop": "Stop loop",
            "pump-on": "Pump on", "pump-off": "Pump off"}


class DashboardSession:
    def __init__(self, chiller: Chiller, recorder=None):
        self.chiller = chiller
        self.recorder = recorder
        self.history = deque(maxlen=600)
        self.failed = False
        self.message = "Reading the chiller…"
        self._lock = RLock()

    def refresh(self, action=None, target_c=None):
        with self._lock:
            if self.failed:
                return None, self.message
            try:
                if action == "set":
                    reported = self.chiller.set_setpoint(target_c)
                    self.message = f"Setpoint readback: {reported:.2f} °C"
                elif action == "run":
                    self.chiller.run()
                    self.message = "Control loop reports Run"
                elif action == "stop":
                    self.chiller.stop()
                    self.message = "Control loop reports Stop"
                elif action in ("pump-on", "pump-off"):
                    self.chiller.set_pump(action == "pump-on")
                    self.message = "Pump readback confirmed"
            except (ValueError, TypeError, PermissionError) as exc:
                # Rejected operator input never constitutes a new telemetry sample.
                self.message = str(exc)
                return None, self.message
            except (OSError, RuntimeError) as exc:
                return self._fail(exc)
            try:
                sample = self.chiller.read_sample()
                if self.recorder:
                    self.recorder.record(sample)
                self.history.append(sample)
                if action is None:
                    self.message = f"Updated {sample.timestamp_utc}"
                return sample, self.message
            except (OSError, RuntimeError) as exc:
                return self._fail(exc)

    def _fail(self, exc):
        self.failed = True
        self.message = f"Session stopped: {exc}. Restart the app to reconnect."
        if self.recorder:
            try:
                self.recorder.record(error=self.message)
            except OSError as log_exc:
                self.message += f" CSV failed: {log_exc}"
        return None, self.message


def create_app(chiller: Chiller, *, recorder=None, interval_s=1.0):
    import plotly.graph_objects as go
    from dash import Dash, Input, Output, State, ctx, dcc, html

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
        return html.Section([
            html.Div([html.H2(title), html.Span(caption, className="muted")],
                     className="section-title"), *children,
        ], className="panel")

    app.layout = html.Main([
        html.Header([
            html.Div([html.P("LABORATORY / THERMAL SYSTEMS", className="eyebrow"),
                      html.H1("NRC400"), html.P("Recirculating chiller", className="subtitle")]),
            html.Div(chiller.transport.backend.upper(), className="backend"),
        ]),
        html.Div(f"{identity.model} · {identity.serial_number} · {identity.firmware}",
                 className="identity"),
        html.Div(id="telemetry", className="metrics"),
        panel("Temperature history", "Last 600 samples · Celsius",
            dcc.Graph(id="trend", config={"displayModeBar": False}),
        ),
        panel("Control", "Writes enabled" if writable else "Read-only session",
            html.Div([
                html.Label("Setpoint / °C", htmlFor="target"),
                dcc.Input(id="target", type="number", value=20, step=0.1,
                          min=chiller.min_c, max=chiller.max_c, disabled=not writable),
                *[html.Button(label, id=key, disabled=not writable,
                              **({"className": "primary"} if key == "run" else {}))
                  for key, label in CONTROLS.items()],
            ], className="controls"),
            html.P(f"Setpoint window {chiller.min_c:g}–{chiller.max_c:g} °C. "
                   "Stop the loop before using pump-only controls.", className="muted"),
            html.Div(id="notice", role="status"),
        ),
        html.Footer([
            html.Span(f"CSV: {recorder.path}" if recorder else "CSV recording off"),
            html.Span("Level status only. Read other alarms on the chiller’s front panel."),
        ]),
        dcc.Interval(id="poll", interval=max(1, round(interval * 1000))),
    ])

    @app.callback(
        [Output(key, prop) for key, prop in [("telemetry", "children"), ("trend", "figure"),
                                           ("notice", "children"), ("poll", "disabled")]],
        [Input("poll", "n_intervals"), *[Input(key, "n_clicks") for key in CONTROLS]],
        State("target", "value"),
    )
    def update(_tick, _set, _run, _stop, _on, _off, target):
        action = ctx.triggered_id
        sample, message = session.refresh(None if action == "poll" else action, target)
        values = [
            ("SUPPLY TEMPERATURE", f"{sample.temperature_c:.2f} °C" if sample else "—"),
            ("REPORTED SETPOINT", f"{sample.setpoint_c:.2f} °C" if sample else "—"),
            ("CONTROL / PUMP", f"{sample.control_loop} / {sample.pump}" if sample else "—"),
            ("COOLANT LEVEL", sample.level if sample else "—"),
        ]
        metrics = [html.Div([html.P(label), html.Strong(value)], className="metric")
                   for label, value in values]
        history = tuple(session.history)
        figure = go.Figure()
        for label, field, color, dash in [
            ("Supply", "temperature_c", "#007d85", "solid"),
            ("Setpoint", "setpoint_c", "#c28a32", "dash"),
        ]:
            figure.add_scatter(x=[s.timestamp_utc for s in history],
                               y=[getattr(s, field) for s in history], name=label,
                               mode="lines", line={"color": color, "width": 2, "dash": dash})
        figure.update_layout(
            template="plotly_white", height=320,
            margin={"l": 55, "r": 20, "t": 15, "b": 45},
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            yaxis_title="°C", xaxis_title="UTC", legend={"orientation": "h", "y": 1.12},
            uirevision="session",
        )
        return metrics, figure, message, session.failed

    return app
