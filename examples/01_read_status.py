"""Connect to the hardware, identify it, and read temperatures and status."""

from tark_nrc.config import create_chiller

COMMAND = "TIM"  # Device clock. Other read commands include DAT, COL, and LVL.

# 1. Connect explicitly. Run from the repository root to find config.toml.
chiller = create_chiller("config.toml")
chiller.connect()
try:
    # connect() queries the actual model, serial number, and firmware.
    print(f"Model:    {chiller.identity.model}")
    print(f"Serial:   {chiller.identity.serial_number}")
    print(f"Firmware: {chiller.identity.firmware}")

    # 2. Read individual temperatures. Python always returns Celsius, even
    # when the front panel displays Fahrenheit. The target is queried live.
    temperature_c = chiller.read_temperature()
    setpoint_c = chiller.read_setpoint()
    print(f"Supply temperature: {temperature_c:.2f} C")
    print(f"Reported setpoint:  {setpoint_c:.2f} C")

    # 3. Fields are queried sequentially; the UTC timestamp follows the last
    # query, rather than a simultaneous measurement.
    sample = chiller.read_sample()
    print(f"Timestamp (UTC):   {sample.timestamp_utc}")
    print(f"Supply:            {sample.temperature_c:.2f} C")
    print(f"Setpoint:          {sample.setpoint_c:.2f} C")
    print(f"Control loop:      {sample.control_loop} (R=running, S=stopped)")
    print(f"Pump:              {sample.pump}")
    print(f"Coolant level:     {sample.level}")
    print(f"Device units:      {sample.device_units}")
    print(f"Coolant code:      {sample.coolant}")
    print(f"As a dictionary:   {sample.to_dict()}")

    # 4. Supply only the three-letter code; the transport adds wire framing.
    # query() reads values; dedicated methods perform control writes.
    reply = chiller.query(COMMAND)
    print(f"{COMMAND}: {reply}")
finally:
    # Release the port, including on an error. Equipment state is retained.
    chiller.close()
