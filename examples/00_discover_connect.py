"""00 · List adapters without probing, then identify one configured NRC400."""

# %% Imports
from pathlib import Path

from serial.tools import list_ports

from tark_nrc import create_chiller

# %% List adapters (this section opens no serial port)
print("Available serial adapters; no devices probed:")
ports = sorted(list_ports.comports(), key=lambda item: item.device)
for port in ports:
    print(f"  {port.device}: {port.description}")
    print(f"    USB serial: {port.serial_number or 'unknown'}; hardware ID: {port.hwid}")
if not ports:
    print("  No serial ports reported by the operating system.")

# %% Choose connection settings
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"
SIMULATE = False  # True uses an independent offline device and ignores CONFIG_PATH.

# %% Connect, identify and release
# The production factory retains the profile's USB/unit binding and framing.
# Construction opens nothing; the context connects using identity queries only.
chiller = create_chiller(None if SIMULATE else CONFIG_PATH, simulate=SIMULATE)
with chiller:
    print(f"Backend: {chiller.transport.backend}; port: {chiller.transport.port}")
    print(f"Model: {chiller.identity.model}")
    print(f"Serial: {chiller.identity.serial_number}")
    print(f"Firmware: {chiller.identity.firmware}")

print("Connection closed. Equipment retains its target, loop and pump state.")
