"""01 · Read status and compare it with the NRC400 panel. No setting writes."""

# %% Imports
import json
from pathlib import Path

from tark_nrc import create_chiller

# %% Choose connection settings
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"
SIMULATE = False

# %% Read one complete sample and raw diagnostics, then release
chiller = create_chiller(None if SIMULATE else CONFIG_PATH, simulate=SIMULATE)
with chiller:
    print(f"Backend: {chiller.transport.backend}; identity: {chiller.identity}")
    sample = chiller.read_sample()
    print(json.dumps(sample.to_dict(), indent=2))
    # Date and clock are raw diagnostics, not trusted acquisition timestamps.
    print(f"Device date (raw): {chiller.query('DAT')}")
    print(f"Device time (raw): {chiller.query('TIM')}")

print("Compare temperature, target, units, loop, pump, level and coolant with the panel.")
print("API temperatures are Celsius even when device_units is F.")
print("CTL=QUIT is retained as reported and accepted after Stop; check the panel separately.")
print("Level OK and reported pump ON do not establish physical flow or complete alarms.")
print("Connection closed; equipment state retained.")
