"""05 · Read raw status, or confirm ONE loop/pump request."""

# %% Imports
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import sleep

from tark_nrc import ProtocolError, create_chiller

# %% Choose connection settings and ONE operation
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"
SIMULATE = False
ACTION = "READ"  # READ sends queries only. Controls: RUN, STOP, PUMP ON or PUMP OFF.
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs"

# %% Connect, inspect, confirm one operation, verify and release
if ACTION not in ("READ", "RUN", "STOP", "PUMP ON", "PUMP OFF"):
    raise ValueError("ACTION must be READ, RUN, STOP, PUMP ON or PUMP OFF")

chiller = create_chiller(None if SIMULATE else CONFIG_PATH, simulate=SIMULATE)
with chiller:
    print(f"Backend: {chiller.transport.backend}; identity: {chiller.identity}")
    before = chiller.read_sample()
    print(f"Target: {before.setpoint_c:.3f} C; supply: {before.temperature_c:.2f} C")
    print(f"Loop: {before.control_loop}; pump: {before.pump}; level: {before.level}")
    if ACTION == "READ":
        # Preserve exact replies, including the firmware's QUIT status.
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"{chiller.transport.backend}_loop_status_{stamp}.jsonl"
        print(f"Read-only diagnostic; no control commands. Saving to: {path.resolve()}")
        with path.open("x", encoding="utf-8") as evidence:
            metadata = {
                "backend": chiller.transport.backend,
                "identity": asdict(chiller.identity),
                "port": chiller.transport.port,
                "sample": before.to_dict(),
            }
            evidence.write(json.dumps(metadata) + "\n")
            evidence.flush()
            for index in range(3):
                reading = {"timestamp_utc": datetime.now(UTC).isoformat()}
                for code in ("CTL", "PMP", "LVL"):
                    reading[code] = chiller.query(code)  # Query only: no comma/data field.
                print(f"Raw replies {index + 1}: {reading!r}")
                evidence.write(json.dumps(reading) + "\n")
                evidence.flush()
                if index < 2:
                    sleep(0.5)
        print("Note the front-panel loop/pump state alongside these raw serial replies.")
    else:
        print("RUN uses the existing target. Prepare the primed loop and inspect panel alarms.")
        print("STOP requests regulation stop; read the pump state separately.")
        print("The device decides whether it accepts each command; the API verifies readback.")
        print("Check heat-load readiness before reducing cooling or circulation.")
        if input(f"Type {ACTION!r} to send ONE request; anything else cancels: ").strip() == ACTION:
            if ACTION == "RUN":
                chiller.run()  # Sends CTL,R once and verifies reported R.
            elif ACTION == "STOP":
                chiller.stop()  # Sends CTL,S once; bounded polling never repeats the write.
            else:
                chiller.set_pump(ACTION == "PUMP ON")  # Sends PMP once and verifies ON/OFF.
            after = chiller.read_sample()
            if ACTION == "STOP":
                matches = after.control_loop in ("S", "QUIT")
            elif ACTION == "RUN":
                matches = after.control_loop == "R"
            else:
                matches = after.pump == ("ON" if ACTION == "PUMP ON" else "OFF")
            if not matches:
                raise ProtocolError(
                    "State changed after verification; inspect the panel. Do not retry."
                )
            print(f"Reported loop: {after.control_loop}; pump: {after.pump}; level: {after.level}")
            print("Compare with the panel and observe flow/temperature separately.")
        else:
            print("Cancelled; no control request sent.")

print("Connection closed. Equipment retains its state; no automatic stop or rollback.")
