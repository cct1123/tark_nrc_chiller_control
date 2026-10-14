"""Command line entry point. Hardware and simulation are explicit choices."""

import argparse
import json
import logging
import math
import time
from contextlib import ExitStack
from dataclasses import asdict

from .config import create_chiller
from .recording import CsvRecorder
from .transport import COMMANDS


def positive(value):
    """Argparse converter for finite positive durations and polling intervals."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Must be a positive finite number")
    return result


def build_parser():
    """Describe the CLI without constructing or connecting a controller."""
    parser = argparse.ArgumentParser(description="NRC400 RS-232 controller; API temperatures in C")
    parser.add_argument("--config", help="TOML configuration")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--simulate", action="store_true", help="Memory-only serial simulator")
    source.add_argument("--port", help="Verified OS serial port, e.g. COM3")
    parser.add_argument(
        "--allow-writes", action="store_true", help="Enable explicit control actions"
    )
    parser.add_argument("--verbose", action="store_true", help="Log serial TX/RX")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, help_, argument=None, **options):
        child = sub.add_parser(name, help=help_)
        if argument:
            child.add_argument(argument, **options)
        return child

    command("ports", "List OS serial ports without opening them")
    command("identify", "Read model, serial number and firmware")
    command("read", "Read temperatures and operating state")
    command("query", "Read one documented command", "code", choices=sorted(COMMANDS))
    command("set", "Set temperature in Celsius and verify readback", "target_c", type=float)
    command("run", "Start the control loop")
    command("stop", "Stop the control loop")
    command("pump", "Pump-only control, with the loop stopped", "state", choices=["on", "off"])
    command("units", "Change the front-panel temperature units", "value", choices=["C", "F"])
    command("time", "Set device time as HH:MM:SS", "value")
    log = command("log", "Record samples to a new CSV")
    log.add_argument("--csv", required=True)
    log.add_argument("--duration", type=positive, default=10.0)
    gui = command("gui", "Open a localhost Dash dashboard")
    gui.add_argument("--csv", help="Optional new CSV to record every displayed sample")
    for child in (log, gui):
        child.add_argument("--interval", type=positive, default=1.0)
    gui.add_argument("--http-port", type=int, default=8050)
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


def execute_command(chiller, args, stack):
    """Dispatch through verified API methods; the stack owns optional CSV cleanup."""
    command = args.command
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
    else:
        recorder = None
        if args.csv:
            recorder = stack.enter_context(
                CsvRecorder(
                    args.csv,
                    chiller.identity,
                    chiller.transport.backend,
                )
            )
        if command == "log":
            log_samples(chiller, recorder, duration_s=args.duration, interval_s=args.interval)
        elif command == "gui":
            from .gui import create_app

            app = create_app(chiller, recorder=recorder, interval_s=args.interval)
            print(f"{chiller.transport.backend}: http://127.0.0.1:{args.http_port}")
            app.run(
                host="127.0.0.1",
                port=args.http_port,
                debug=False,
                use_reloader=False,
                threaded=False,
            )


def main(argv=None):
    """Select a backend, own one connection, dispatch, and close on every exit path."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.command == "ports":
        from serial.tools.list_ports import comports

        for port in comports():
            print(f"{port.device}: {port.description}")
        return 0
    if not args.simulate and args.port is None and args.config is None:
        parser.error("Select --simulate, --port or --config explicitly")
    try:
        with ExitStack() as stack:
            chiller = stack.enter_context(
                create_chiller(
                    args.config,
                    simulate=args.simulate,
                    port=args.port,
                    allow_writes=args.allow_writes,
                )
            )
            execute_command(chiller, args, stack)
        return 0
    except KeyboardInterrupt:
        print("Session closed. The chiller retains its state; stop explicitly if required.")
        return 0
    except (OSError, RuntimeError, ValueError, TypeError, ImportError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
