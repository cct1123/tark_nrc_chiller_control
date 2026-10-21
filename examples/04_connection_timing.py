"""04 · Time one idle connect/close cycle. No control or monitoring commands."""

# %% Imports
from pathlib import Path
from time import perf_counter

from tark_nrc import create_chiller

# %% Choose connection settings
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"
SIMULATE = False

# %% Time connection and release, including on connection failure
chiller = create_chiller(None if SIMULATE else CONFIG_PATH, simulate=SIMULATE)
print(f"Backend: {chiller.transport.backend}; timeout: {chiller.transport.settings.timeout_s:g} s")
connect_ok = close_ok = False
try:
    started = perf_counter()
    try:
        chiller.connect()  # Opens the selected port and queries MOD, SSN and VER.
        connect_ok = True
    finally:
        print(f"connect(): {(perf_counter() - started) * 1000:.3f} ms | success={connect_ok}")
    print(f"Identity: {chiller.identity}")
finally:
    started = perf_counter()
    try:
        chiller.close()  # Attempt release even if connection/identification failed.
        close_ok = True
    finally:
        print(f"close():   {(perf_counter() - started) * 1000:.3f} ms | success={close_ok}")

print("Idle cycle complete. This does not measure shutdown during active I/O.")
print("Connection closure does not stop equipment.")
