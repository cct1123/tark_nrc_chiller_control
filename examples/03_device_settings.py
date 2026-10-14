"""Set hardware display units and the device clock, verifying each change."""

from tark_nrc.config import create_chiller

DISPLAY_UNITS = "C"  # Set to "F" to select Fahrenheit on the device.
DEVICE_TIME = "12:00:00"  # Edit before running; 24-hour HH:MM:SS device time.

# Both settings are hardware writes; this explicitly overrides the config flag.
with create_chiller("config.toml", allow_writes=True) as chiller:
    # 1. Change the front-panel units. The Python temperature API stays Celsius.
    chiller.set_units(DISPLAY_UNITS)
    print(f"Verified display units: {DISPLAY_UNITS}")
    print(f"Supply temperature from Python: {chiller.read_temperature():.2f} C")

    # 2. Set the clock. Verified readback may advance slightly as the clock runs.
    reported_time = chiller.set_time(DEVICE_TIME)
    print(f"Verified device time: {reported_time}")
