# Hardware API tutorials

Four plain scripts group the NRC400 API by theme. Each runs from top to bottom
using the hardware selected by `config.toml`, with numbered tutorial comments and
editable constants. There are no `main()` functions or simulator selections.

## Set up once

From the repository root, install the package into your environment:

```powershell
python -m venv .venv
$nrcPython = ".\.venv\Scripts\python"
& $nrcPython -m pip install -c requirements-tested.txt ".[gui]"
```

Keep an existing verified `config.toml`. Otherwise copy `config.example.toml` to
`config.toml` and follow the [commissioning guide](../docs/commissioning.md) to
enter the verified port, framing, write-reply behavior, temperature limits, and
optional serial-number binding. Begin with `control.allow_writes = false`.

Run scripts from the repository root so `"config.toml"` resolves to your local
configuration. Close other programs using the serial port first. Choose the
tutorial your procedure needs; the numbers indicate learning order.

## 1. Connect and read

[01_read_status.py](01_read_status.py) demonstrates:

- `connect()`, actual `identity`, and `close()` in a `finally` block.
- `read_temperature()` and `read_setpoint()` in Celsius, regardless of display units.
- `read_sample()` with UTC timestamp, temperatures, loop, pump, level, units, and coolant.
- `Sample.to_dict()` for your own data pipeline.
- `query(COMMAND)` for raw text; the default `TIM` reads the device clock.

```powershell
& $nrcPython examples/01_read_status.py
```

These are queries only. Samples read fields sequentially and are timestamped
after the final query; they are not simultaneous measurements.

## 2. Control regulation and the pump

[02_control_loop_and_pump.py](02_control_loop_and_pump.py) demonstrates
`set_setpoint()`, `run()`, `stop()`, and both `set_pump(True)` and
`set_pump(False)` in one supervised session.

Edit `TARGET_C` first (default 20 C, within configured limits). The script pauses
for Enter before applying the target and starting regulation, before stopping,
and before each pump-only action. Regulation continues while you perform your
experiment at the Stop prompt; the pump stays on at the Pump off prompt.

```powershell
& $nrcPython examples/02_control_loop_and_pump.py
```

Setting a target alone does not start regulation. Run requires coolant level OK
and also activates the pump. Stop stops the loop; pump control is separate.
Pump-only changes require a stopped loop, and Pump on also requires level OK.

## 3. Change device settings

[03_device_settings.py](03_device_settings.py) demonstrates `set_units()` and
`set_time()`. Edit `DISPLAY_UNITS` (`C` or `F`) and `DEVICE_TIME` (24-hour
`HH:MM:SS`) before running. It applies both settings, prints verified readbacks,
and shows that Python temperature reads remain Celsius. The clock may advance
slightly before its readback.

```powershell
& $nrcPython examples/03_device_settings.py
```

Both control/settings scripts explicitly pass `allow_writes=True`, **overriding
`allow_writes = false` in `config.toml`**. Other configured settings, including
unit binding and temperature limits, still apply. The API verifies each write
by querying the device. Reported states do not prove physical circulation or
cooling. On a communication/readback failure, inspect the equipment before
choosing another action; these scripts do not retry failed writes.

Closing a connection, Ctrl+C, or a script error leaves the equipment in its
current state. It does not automatically stop regulation or turn off the pump.

## 4. Record and watch

[04_record_and_dashboard.py](04_record_and_dashboard.py) demonstrates
`CsvRecorder.record()` followed by `create_app()` on the same hardware connection.
It saves ten samples to a new UTC-named CSV in `outputs/`, closes that file, and
then serves live telemetry at <http://127.0.0.1:8050> until Ctrl+C.

```powershell
& $nrcPython examples/04_record_and_dashboard.py
```

Edit `SAMPLE_COUNT` and `INTERVAL_S` for the recording length; serial query time
adds to the one-second default pause. Each row includes sample values, identity,
and backend, and is flushed to disk. Existing files are never overwritten. A read
or disk error ends the script and closes resources. Set `SHOW_DASHBOARD = False`
to finish after recording. The dashboard does not append to this completed CSV.

The `gui` extra is required by this script. Dashboard controls follow
`control.allow_writes` in `config.toml`; initial read-only settings disable them.
Set the flag to `true` before launching when your procedure calls for interactive
control. Stop loop and Pump off are explicit dashboard actions. Closing the
dashboard releases the port without changing equipment state.
