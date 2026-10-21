"""02 · Confirm one Celsius target change and verify its reported value."""

# %% Imports
from math import isfinite
from pathlib import Path

from tark_nrc import ProtocolError, create_chiller

# %% Choose connection settings and an approved target
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"
SIMULATE = False
TARGET_C = 20.0  # Illustrative only. Choose a modest change approved for your setup.

# %% Connect, confirm, change one target, verify and release
if isinstance(TARGET_C, bool) or not isfinite(TARGET_C):
    raise ValueError("TARGET_C must be a finite number in Celsius")

chiller = create_chiller(None if SIMULATE else CONFIG_PATH, simulate=SIMULATE)

with chiller:
    print(f"Backend: {chiller.transport.backend}; identity: {chiller.identity}")
    before = chiller.read_sample()
    print(f"Current target: {before.setpoint_c:.3f} C; supply: {before.temperature_c:.2f} C")
    print(f"Loop: {before.control_loop}; pump: {before.pump}; level: {before.level}")
    print("Check the filled/primed circuit, panel, coolant and approved temperature limits.")
    token = f"SET {TARGET_C:g}"
    if (
        input(f"Type {token!r} to send one target request; anything else cancels: ").strip()
        == token
    ):
        # Send once; this method already checks readback and device-unit rounding.
        confirmed_c = chiller.set_setpoint(TARGET_C)
        reported_c = chiller.read_setpoint()
        if abs(reported_c - confirmed_c) > 0.001:
            raise ProtocolError(
                "Target drifted after verification; inspect the panel. Do not retry."
            )
        print(f"Requested: {TARGET_C:g} C; confirmed rounded target: {reported_c:.3f} C")
        print("Compare with the panel. Readback does not establish thermal settling.")
    else:
        print("Cancelled; no target request sent.")

print("Connection closed; no automatic target restoration or equipment stop was sent.")
