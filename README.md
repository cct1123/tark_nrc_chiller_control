# NRC400 chiller controller

Python control, a local dashboard, CSV recording and an offline simulator for the
Tark NRC400-T0-00-PC2. The chiller regulates temperature internally; this package
reads its state and sends verified commands over RS-232.

## Install and try

Python 3.11 or newer is required. From the repository root:

```powershell
python -m pip install .
tark-nrc --simulate read
tark-nrc --simulate gui
```

Alternatively, with [uv](https://docs.astral.sh/uv/):

```powershell
uv sync
uv run tark-nrc --simulate gui
```

Open <http://127.0.0.1:8057> and press **Connect**. The dashboard starts
disconnected. Apply a setpoint, then press **Run loop**; the same button becomes
**Stop loop** when running. A separate pump button switches between **Pump on**
and **Pump off**. Labels follow device readings and name the action a click sends.
**Shut down GUI** closes the connection and local server; the chiller retains its
loop and pump state.

Simulation never opens a hardware port. Each process has a separate simulated
device with an illustrative 12-second response and 24 °C idle temperature.

## Connect hardware

Follow [commissioning](docs/commissioning.md), copy `config.example.toml` to
`config.toml`, and enter the verified serial port. Check identity and readings:

```powershell
tark-nrc ports
tark-nrc --config config.toml identify
tark-nrc --config config.toml read
tark-nrc gui
```

The GUI loads `config.toml` from the current directory unless `--config` or an
explicit backend is supplied. All controls become available after Connect;
there is no write-enable flag or read-only session. Hardware selection never
falls back to simulation.

The link uses 115200 baud, 8N1, without flow control. Configure verified line
endings, timeouts and write replies for your unit. Optionally bind
`serial.usb_serial_number` to one USB adapter and
`control.expected_serial_number` to the chiller itself. `--port` overrides USB
adapter selection. Your local configuration is excluded from Git.

All API temperatures are Celsius, including when the panel displays Fahrenheit.
The application imposes no setpoint window, coolant-level prerequisite or loop-state
prerequisite. Users choose actions directly; the device decides which commands it
accepts. Commands are sent once and readback is checked. Errors are shown without
permanently locking later actions; commands are never replayed automatically.

The dashboard retains 600 samples. `CTL=QUIT` is retained as a firmware status
and does not disable controls. A device rejection or mismatched readback remains
visible while later commands stay available. A CSV error stops recording while
readings and controls continue. A framing/connection failure closes the port;
press **Connect** to reopen it without restarting the app. Closing or reconnecting
sends no control commands.

The `[control]` section now contains only the optional `expected_serial_number`.
Remove obsolete `allow_writes`, `min_c` and `max_c` keys from older profiles.

## Python API and recording

For guided hardware runs, see the [standalone tutorial scripts and notebooks](examples/README.md).
They cover discovery, panel comparisons, confirmed target changes, CSV recording,
connection timing, separate loop/pump controls and the local dashboard, with a
hardware test record checklist and an explicit offline rehearsal option.

```python
from tark_nrc import CsvRecorder, create_chiller

# Use create_chiller("config.toml") for hardware access.
with create_chiller(simulate=True) as chiller:
    print(chiller.identity)
    chiller.set_setpoint(20.0)
    chiller.run()
    with CsvRecorder("session.csv", chiller.identity, chiller.transport.backend) as csv:
        csv.record(chiller.read_sample())
    chiller.stop()
    chiller.set_pump(False)
```

CSV files must have new names; existing measurements are never overwritten.
To record from the CLI or dashboard:

```powershell
tark-nrc --simulate log --duration 5 --interval 1 --csv demo.csv
tark-nrc --simulate gui --csv dashboard.csv
```

CLI commands: `ports`, `identify`, `read`, `query CODE`, `set CELSIUS`, `run`,
`stop`, `pump on|off`, `units C|F`, `time HH:MM:SS`, `log`, `gui`.
Use `tark-nrc --help` or `tark-nrc COMMAND --help` for options.

## Package and checks

There are three implementation modules, plus package imports and a small
`python -m tark_nrc` entry point:

| Module | Responsibility |
|---|---|
| `controller` | Serial protocol, verified controls, TOML configuration and CSV recording |
| `gui` | Local Dash dashboard and command-line interface |
| `simulator` | Offline serial peer using the controller's real transport |

Imports open no ports or threads. Use one operator and one serial owner.

To understand the code, follow one simulated read in this order:

1. `gui.main()` parses `--simulate read`, calls `create_chiller()`, and connects.
2. `controller.Chiller.connect()` queries model, serial number and firmware.
   `execute_command()` then calls `Chiller.read_sample()`.
3. Each reading goes through `Chiller.query()` and `SerialTransport.request()`.
   The transport sends ASCII bytes and reads a reply. In simulation those bytes
   go to `simulator.SimulatedSerial.write()` instead of a physical port.
4. `read_sample()` returns a `Sample` with named fields; the CLI prints it as JSON.
   The `finally` block in `main()` closes the connection even after an error.

For the dashboard, start at `gui.create_app()`. Its nested `update()` callback
chooses the requested action and calls `DashboardSession.refresh()`. The session
performs the action, reads fresh state, checks the result, and saves it to history
and optional CSV. The callback then puts those values on the page. Button clicks
request actions; the connection state determines whether buttons are enabled.
When adding a control, keep `CONTROLS`, the callback arguments/outputs,
`_apply_action()` and the returned display values in agreement.

| Change you want to make | Start here | Relevant tests |
|---|---|---|
| Parse a new firmware reply | `Chiller._read_choice()` / `_read_temperature_c()` | `tests/test_hardware_replies.py` |
| Add or change a device setting | A public `Chiller` method and `SimulatedSerial.write()` | `tests/test_command_contract.py`, `tests/test_controller.py` |
| Change the screen or button behavior | `create_app()` / `DashboardSession` | `tests/test_workflows.py` |
| Change serial framing or timeout handling | `SerialTransport.request()` | `tests/test_transport.py` |

Three details matter when editing control code:

- `with chiller:` connects and closes the port; closing does not stop equipment.
  `with CsvRecorder(...):` closes only the CSV file.
- Setting methods send once, then check readback. Errors report an unconfirmed
  result without replaying the command or locking later user actions. Keep device
  writes and readback checks together in `Chiller` methods.
- The `RLock` allows a method holding the lock to call another method on the same
  object. It keeps an entire read/control operation together; it starts no thread.

Begin with a simulator-only change, such as a dashboard label, and run its relevant
tests. For protocol changes, expected command bytes in `test_command_contract.py`
are written independently of the simulator so the two cannot silently share the
same mistake. Follow commissioning before testing new behavior on equipment.

Development tools are optional:

```powershell
uv sync --group dev
uv run --group dev pytest
uv run --group dev ruff check src tests examples
uv run --group dev ruff format --check src tests examples
uv build
```

The [protocol reference](docs/protocol.md) documents command verification and
firmware assumptions. [Source provenance](docs/sources/README.md) identifies the
manual. The [2026-10-14 hardware record](docs/hardware-session-2026-10-14.md)
documents partial bench checks; the refactor's automated checks are offline and
do not complete [physical commissioning](docs/commissioning.md).
