# NRC400 hardware tutorials

Seven standalone scripts cover discovery → read → controlled change →
record → timing → loop/pump control → dashboard. Examples 00–05 also have
standalone notebooks. Each uses the NRC400 API and serial profile.

| Script | Notebook | Operation / expected result |
| --- | --- | --- |
| [00_discover_connect.py](00_discover_connect.py) | [00_discover_connect.ipynb](00_discover_connect.ipynb) | List OS adapter metadata without probing; query the configured unit's model, serial and firmware |
| [01_read_status.py](01_read_status.py) | [01_read_status.ipynb](01_read_status.ipynb) | Read a complete sample plus raw date/time; compare with the panel |
| [02_set_temperature.py](02_set_temperature.py) | [02_set_temperature.ipynb](02_set_temperature.ipynb) | Confirm one target request; compare rounded target readback |
| [03_record_csv.py](03_record_csv.py) | [03_record_csv.ipynb](03_record_csv.ipynb) | Record timestamped telemetry to a new CSV; stop on the first read/recording error |
| [04_connection_timing.py](04_connection_timing.py) | [04_connection_timing.ipynb](04_connection_timing.ipynb) | Measure one idle connect/close cycle, including identity queries |
| [05_run_stop_pump.py](05_run_stop_pump.py) | [05_run_stop_pump.ipynb](05_run_stop_pump.ipynb) | Capture raw loop status read-only, or confirm one loop/pump operation |
| [06_dashboard.py](06_dashboard.py) | Script only | Open the local dashboard disconnected; controls become available after Connect |

Each file contains its own settings and connection/cleanup code. The examples use
the production `create_chiller()` factory so USB adapter selection, chiller serial
binding, framing and timeouts follow your TOML profile. There is no
shared tutorial helper or separate serial implementation.

## 1. Prepare the environment and hardware profile

From the repository root, using Python 3.11 or newer:

```powershell
uv sync --locked
Copy-Item config.example.toml config.toml  # First setup only; preserve an existing profile.
uv run --locked tark-nrc ports
```

Alternatively, install the checkout with `python -m pip install -e .`, then run
scripts with `python` instead of `uv run --locked python`.

Follow [physical commissioning](../docs/commissioning.md) before equipment runs.
In `config.toml`, enter the **verified** port or exact USB adapter serial. Optionally
bind `control.expected_serial_number` to the chiller. Adapter metadata identifies
the adapter, not the attached chiller. No example scans/probes candidate ports or
guesses a COM number. Discovery lists adapters before trying to load the profile,
so it remains useful before configuration is complete.

The NRC400 link is 115200 baud, 8N1, without flow control. Retain your verified
`line_ending`, `timeout_s`, `write_response` and `write_delay_s`; see the
[protocol assumptions](../docs/protocol.md#hardware-assumptions). Example defaults
are assumptions, not a replacement for measuring the fitted unit's replies.
Do not change acknowledgement policy merely to get past a failed write.

The current `[control]` section supports only `expected_serial_number`. Remove
obsolete `allow_writes`, `min_c` and `max_c` entries from older profiles before
running these examples; the factory rejects them as unknown settings. Choose
targets appropriate to the actual coolant, firmware and experiment. The API
checks finite input and verifies device readback; it imposes no target range,
level prerequisite or loop-state prerequisite. The device decides whether a
command is accepted.

Examples 00, 01, 03 and 04 are read-only because they call query methods only.
Examples 02 and 05 retain a tutorial prompt to confirm one requested change;
this is local example behavior. All API controls are available after connection,
and the dashboard exposes them without a separate permission switch.
All API targets and CSV temperatures are **Celsius**, including when the panel
displays Fahrenheit.

Before connecting, close other dashboards, notebooks and terminal programs that
own the port. Check coolant, priming, leaks, hoses and heat-load protection at the
equipment. Use one serial owner and one supervising operator.

## 2. Run scripts or notebooks

Edit the settings near the top of the selected file, then run from this checkout:

```powershell
uv run --locked python examples/00_discover_connect.py
uv run --locked python examples/01_read_status.py
```

Scripts locate `config.toml` beside the repository README, independently of the
terminal's working directory. Change `CONFIG_PATH` for another profile. Exceptions
are left visible in the terminal. Ctrl+C interrupts a running operation and closes
resources through its context/finally block, provided Python can finish cleanup.

To launch the optional notebook environment from the repository root:

```powershell
uv run --locked --with jupyterlab --with ipykernel python -m jupyterlab
```

Open a notebook under `examples/` and select the Python kernel from this environment.
Each notebook includes a source-path bootstrap for launch from the repository root
or `examples/`; from elsewhere, use an installed package and set absolute
`CONFIG_PATH`/`OUTPUT_DIR` values. Run cells top to bottom. Restart the kernel after
driver changes. Each operation cell connects and closes in the same cell; do not
split it or rerun setup over an unfinished operation. No notebook output is shipped
as if it were an equipment measurement.

For an offline rehearsal, set `SIMULATE = True` in the selected script/notebook.
This ignores the hardware profile, opens no physical port, and labels readings
`simulator`. The simulator starts with a 20 °C target; each example starts
a fresh simulated device. State does not carry between files. To try a changed
target offline, choose e.g. `TARGET_C = 21.0` in example 02. Simulator behavior is
illustrative and does not establish physical flow, cooling or bench acceptance.

## 3. Read-only checks: examples 00, 01, 03 and 04

Start with 00. Match model, serial and firmware against the unit label/panel and
record them. Then run 01 and compare supply temperature, target, display units,
loop (`R`/`S`), pump (`ON`/`OFF`), level and coolant report with the panel. A reported
`CTL=QUIT` is preserved as a firmware status; readings and controls remain available.
Stop accepts S/QUIT based on the operator's stopped-state observation on firmware
1.0.21; Run still requires R. Neither reply establishes physical pump-off. Queries
are sequential, so a sample is not a simultaneous snapshot. Raw device date/time
may be uncalibrated; acquisition timestamps come from the host's UTC clock.
Coolant codes do not verify physical tank contents, and level OK does not report
a complete alarm state.

Example 03 defaults to ten seconds at a requested one-second start-to-start
interval. Set `DURATION_S = 600.0` for the commissioning ten-minute recording.
Files go to repository `outputs/`, named with backend and UTC time including
microseconds. `CsvRecorder` refuses existing files and flushes each row. Rows
include host UTC, backend, Celsius temperatures, loop/pump/level/units/coolant,
model, serial, firmware and an error column.

The first read error adds a row with blank measurements, then raises and closes
the connection. Recording errors also propagate; a disk failure may prevent an
error row. Prior flushed rows remain available. Ctrl+C may leave a partial record;
check terminal outcome and rows before treating it as complete. Serial transaction
time can exceed the requested interval or end time; neither an exact sample count
nor an exact duration is guaranteed. Check timestamp gaps and the error column.

Example 04 prints separate `connect()` and `close()` durations plus success flags.
Connect includes opening the selected port and querying MOD, SSN and VER. Close
is attempted even when identification fails. This is one idle API timing result,
not a throughput benchmark, per-command timeout estimate or active-I/O shutdown test.

## 4. Controlled changes: examples 02 and 05

Supervise these actions at the panel with the filled/primed loop ready and heat
load managed. A successful read does not establish readiness for a control action.

In 02, choose a modest approved `TARGET_C`, review identity and current state,
then type the displayed `SET ...` token exactly. Any other response cancels.
The setter transmits one target and verifies it. The extra read compares with
the confirmed rounded value because the device accepts one decimal in its display
units, which can introduce Celsius quantization when displaying Fahrenheit.
The example does not start/stop the loop, restore the old target or wait for
thermal settling. Restoring a target is a separate reviewed request.

In 05, the default `ACTION = "READ"` sends queries only. It prints the complete
sample, then captures three rounds of exact CTL/PMP/LVL replies in a new
`outputs/<backend>_loop_status_<UTC>.jsonl` file, flushing each row. Record the
front-panel loop/pump state alongside these readings. This diagnostic sends no
Stop, Run or pump command and retains QUIT verbatim. A failed read leaves
any previously flushed rows available.

To request a control operation, choose one of the following `ACTION` values and
type that action at the prompt:

| ACTION | Request and software check | Physical observation |
| --- | --- | --- |
| `RUN` | Uses existing target; sends CTL,R once and verifies reported R | Panel state, actual circulation and temperature response |
| `STOP` | Sends CTL,S once; accepts S/QUIT, polling while R | Panel state and retained pump/fan behavior |
| `PUMP ON` | Sends PMP,ON once and verifies ON | Actual flow, priming and leaks |
| `PUMP OFF` | Sends PMP,OFF once and verifies OFF | Actual cessation of circulation and heat-load readiness |

Each control operation requires typed confirmation. Choose an
operation appropriate to the observed starting state. The API does not precheck
level or loop state before sending Run or pump requests. Follow the equipment's
operating procedure and observe its response. The simulator can reject a pump
change while running; that is its device reply, not a controller prerequisite.
A rejected pump operation does not automatically stop the loop.
There is no automatic Run → Stop → Pump off cycle.

If a setting or its readback fails or is interrupted, the request may have applied.
Do not replay it or automatically restore a previous state. Inspect the front panel
and equipment first. A readback mismatch does not lock later API commands. A
transport failure invalidates the serial connection and needs explicit reconnect.
These standalone examples close and raise on an error. Reconnecting reidentifies
the unit without replaying a control command. Use the front
panel if communication is unavailable. Stop does not guarantee pump-off.

## 5. Dashboard: example 06

```powershell
uv run --locked python examples/06_dashboard.py
```

Open <http://127.0.0.1:8057> and press **Connect**. The dashboard starts disconnected
and polls after connection. All target, loop and pump controls become available
while connected, including when the reported loop state is QUIT. Each button
requests one operation; there is no separate write-enable option or state-based
control lock. The loop button switches between **Run loop** and **Stop loop**;
the pump button switches between **Pump on** and **Pump off**. Labels show the
next requested action and follow device readback. Set `CSV_PATH` to a new file
before starting the server for optional recording; relative paths follow the
terminal's working directory.

Check that the displayed backend/identity matches the intended run. Input and
readback errors are displayed without locking later actions. A communication
failure closes the connection; inspect the equipment, then press **Connect** to
reidentify the same unit. No command is replayed and an app restart is not needed.
A CSV write failure stops recording while readings and controls remain available;
the page keeps showing the recording error. Start a new dashboard session with a
new CSV path to resume recording. If CSV creation fails during Connect, resolve
the path problem before reconnecting. A different chiller is rejected on reconnect.

Use **Shut down GUI** or Ctrl+C to end the server and release the connection.
Closing the browser leaves the server running. The optional `SERIAL_TRACE` logs
exact TX/RX, including identity, to the terminal; save the terminal transcript
separately when evidence is needed.

**Connection closure, kernel exit and GUI shutdown do not stop the equipment.**
Complete the physical shutdown procedure separately, and confirm loop/pump state
at the panel. Forced process/kernel termination cannot guarantee port cleanup.

## 6. Record a hardware test result

Use a new run folder under `outputs/` for your CSV, terminal transcript and notes.
Record operator/date, unit serial/firmware, adapter/cable, profile/framing, coolant,
approved target bounds, load and ambient conditions. Keep software and physical
observations in separate columns:

| Check | Evidence to retain | Acceptance / remaining work |
| --- | --- | --- |
| Identity and panel comparison | 00/01 output and panel observations | Exact intended unit; readings/units/state agree |
| Read-only monitoring | 03 CSV and terminal outcome | Inspect every error and timestamp gap; compare independent readings |
| Modest target change | 02 before/request/rounded readback and panel | Target agrees; separately record settling and persistence |
| Loop and pump controls | One 05 run per selected operation, with initial/final state | Serial agreement plus observed panel, flow and pump/fan behavior |
| Connection release | 04 timings and subsequent intentional reconnect | Port released; retained physical state recorded |
| Physical commissioning | Completed [commissioning record](../docs/commissioning.md#acceptance-record) | Flow/thermal capacity, protection, alarms and supervised link-loss work remain separate |

Mark results pass/fail/not performed with the actual observation. Never infer
complete commissioning from a serial readback, a simulator run or the offline test
suite. The [2026-10-14 hardware record](../docs/hardware-session-2026-10-14.md)
contains partial prior evidence. On 2026-10-21 the operator reported successful
hardware runs of notebooks 00–04. The saved ten-sample CSV from notebook 03 reports
`CTL=QUIT`, pump `OFF` and level `OK` on firmware 1.0.21. Notebook 05's Stop request
then failed the requirement for an S/Stopped readback while the operator observed
the device already stopped. The operator then confirmed the error's last readback
was QUIT. Stop now accepts that terminal reply, retaining QUIT in samples and
checking it consistently in the notebook and dashboard. This software correction
has offline coverage; its hardware retest and complete physical acceptance remain
outstanding.
