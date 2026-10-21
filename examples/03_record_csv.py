"""03 · Record read-only telemetry to a new CSV for a fixed duration."""

# %% Imports
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic, sleep

from tark_nrc import CsvRecorder, create_chiller

# %% Choose connection and recording settings
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"
SIMULATE = False
DURATION_S = 10.0  # Use 600.0 for the commissioning ten-minute read-only record.
INTERVAL_S = 1.0  # Minimum start-to-start interval; serial transactions may take longer.
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs"

# %% Connect, record sequential samples and release
for name, value in (("DURATION_S", DURATION_S), ("INTERVAL_S", INTERVAL_S)):
    if isinstance(value, bool) or not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number of seconds")

chiller = create_chiller(None if SIMULATE else CONFIG_PATH, simulate=SIMULATE)
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
csv_path = OUTPUT_DIR / f"{chiller.transport.backend}_{stamp}.csv"
with chiller:
    print(f"Backend: {chiller.transport.backend}; identity: {chiller.identity}")
    print(f"Recording to: {csv_path.resolve()}")
    # CsvRecorder uses exclusive creation and flushes each row; no overwrites.
    with CsvRecorder(csv_path, chiller.identity, chiller.transport.backend) as recorder:
        deadline = monotonic() + DURATION_S
        while monotonic() < deadline:
            started = monotonic()
            try:
                sample = chiller.read_sample()
            except (OSError, RuntimeError) as exc:
                recorder.record(error=str(exc))  # Blank measurements for this failed read.
                raise  # Stop at the first failed read; no automatic reconnect/retry.
            recorder.record(sample)
            print(
                f"{sample.timestamp_utc} | {sample.temperature_c:.2f} C | "
                f"target {sample.setpoint_c:.2f} C | {sample.control_loop}/{sample.pump}"
            )
            remaining = min(INTERVAL_S - (monotonic() - started), deadline - monotonic())
            sleep(max(0.0, remaining))
        print(f"Saved {recorder.rows} rows.")

print("CSV and connection closed; equipment state retained.")
