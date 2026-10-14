# NRC400 chiller controller

Python API, CLI, serial simulator, CSV recorder and local Dash dashboard for the
Tark NRC400-T0-00-PC2. A supervised happy-path prototype; hardware commissioning
is pending. The chiller's internal controller performs thermal regulation.

## Install and try

Python 3.11+, Windows PowerShell (Linux/macOS: use `.venv/bin/python`):

```powershell
python -m venv .venv
$nrcPython = ".\.venv\Scripts\python"
& $nrcPython -m pip install -c requirements-tested.txt ".[gui]"
& $nrcPython -m tark_nrc --simulate read
& $nrcPython -m tark_nrc --simulate log --duration 5 --csv outputs/demo.csv
& $nrcPython -m tark_nrc --simulate --allow-writes gui
```

Open <http://127.0.0.1:8050>, apply a target and press Run. Each process has its own
simulation (12 s first-order response, 24 °C idle; not measured performance).
CSV recording requires a new filename on every run.

## Hardware and control

Follow [commissioning](docs/commissioning.md), copy `config.example.toml` to
`config.toml`, enter the verified port and begin with `allow_writes = false`.
Hardware selection never falls back to simulation.

```powershell
$nrcPython = ".\.venv\Scripts\python"
& $nrcPython -m tark_nrc ports
& $nrcPython -m tark_nrc --config config.toml identify
& $nrcPython -m tark_nrc --config config.toml read
& $nrcPython -m tark_nrc --config config.toml gui --csv outputs/first-read.csv
& $nrcPython -m tark_nrc --config config.toml --allow-writes set 20
& $nrcPython -m tark_nrc --config config.toml --allow-writes run
& $nrcPython -m tark_nrc --config config.toml --allow-writes stop
& $nrcPython -m tark_nrc --config config.toml --allow-writes pump off
```

API/CLI temperatures are Celsius; default limits are 10–40 °C. Verify coolant/
firmware before expanding them. Controls verify readback; Stop affects the loop,
with separate pump control. Disconnect/close retains equipment state; no retries.

## Python and CLI

Use [examples/lab.py](examples/lab.py) for configuration-based integration, or:

```python
from tark_nrc import Chiller, SerialSettings, SerialTransport
with Chiller(SerialTransport(SerialSettings(port="COM3")), allow_writes=True) as chiller:
    print(chiller.identity, chiller.read_sample())
    chiller.set_setpoint(20.0)
    chiller.run()
    # Perform the experiment; choose shutdown according to its needs.
    chiller.stop()
    chiller.set_pump(False)
```

Methods: `connect`, `close`, `query`, `read_temperature`, `read_setpoint`,
`read_sample`, `set_setpoint`, `run`, `stop`, `set_pump`, `set_units`, `set_time`.
CLI commands: `ports`, `identify`, `read`, `query CODE`, `set CELSIUS`, `run`,
`stop`, `pump on|off`, `units C|F`, `time HH:MM:SS`, `log`, `gui`; use `--help`.

Imports open nothing; one locked connection owns access. Avoid competing serial
programs/manual unit changes. Samples are sequential, timestamped afterward, with
SCF checked twice. The dashboard keeps 600 samples, optional CSV, and stops on
device/disk failure; investigate before restarting. It runs on localhost in one
process/thread without debug/reloader. No external PID, background service/database,
unattended fault handling, multiuser control or ramps are included.

## Reference and checks

- [Protocol, command audit and unresolved firmware details](docs/protocol.md)
- [Installation and physical acceptance](docs/commissioning.md)
- [Software validation](docs/validation.md) and [source provenance](docs/sources/README.md)

Install `".[gui,dev]"` for development, then run:

```powershell
$nrcPython = ".\.venv\Scripts\python"
& $nrcPython -m pytest
& $nrcPython -m ruff check src tests examples
& $nrcPython -m build
```
