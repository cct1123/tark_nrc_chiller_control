"""Record ten hardware samples, then watch the same connection in a dashboard."""

import time
from datetime import datetime, timezone
from pathlib import Path

from tark_nrc.config import create_chiller
from tark_nrc.gui import create_app
from tark_nrc.recording import CsvRecorder

SAMPLE_COUNT = 10
INTERVAL_S = 1.0  # Pause after each read; query time adds to the sampling period.
SHOW_DASHBOARD = True  # Set False to finish after saving the CSV.

# 1. Choose a new output file. CsvRecorder refuses to overwrite existing data.
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
csv_path = Path("outputs") / f"hardware_{stamp}.csv"

with create_chiller("config.toml") as chiller:
    # 2. Record samples. Each flushed row includes device identity and backend.
    # A read or disk error ends the script and closes its resources.
    with CsvRecorder(csv_path, chiller.identity, chiller.transport.backend) as recorder:
        for index in range(SAMPLE_COUNT):
            sample = chiller.read_sample()
            recorder.record(sample)
            print(f"{index + 1}/{SAMPLE_COUNT}: {sample.temperature_c:.2f} C")
            if index + 1 < SAMPLE_COUNT:
                time.sleep(INTERVAL_S)
    print(f"CSV saved to: {csv_path.resolve()}")

    # 3. The CSV is now closed. Watch telemetry using the same serial owner.
    # Dashboard controls follow control.allow_writes in config.toml.
    if SHOW_DASHBOARD:
        app = create_app(chiller, interval_s=1.0)
        print("Open http://127.0.0.1:8050 in your browser. Press Ctrl+C to exit.")
        # One process/thread owns the port; disable the development reloader.
        app.run(host="127.0.0.1", port=8050, debug=False, use_reloader=False, threaded=False)

# Closing the dashboard releases the port; Stop/Pump off remain explicit actions.
