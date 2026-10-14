"""Set a target, run/stop regulation, and demonstrate pump-only control."""

from tark_nrc.config import create_chiller

TARGET_C = 20.0  # Choose a target within your config.toml min_c / max_c limits.

# Explicitly permit hardware writes, overriding the config's allow_writes flag.
# The context manager connects on entry and releases the port on exit.
with create_chiller("config.toml", allow_writes=True) as chiller:
    # 1. Set a temperature target. This alone does not start regulation.
    print(f"Existing setpoint: {chiller.read_setpoint():.2f} C")
    input(f"Press Enter to apply {TARGET_C:g} C and start regulation; Ctrl+C to cancel. ")
    reported_c = chiller.set_setpoint(TARGET_C)
    print(f"Verified setpoint: {reported_c:.2f} C")

    # 2. Start regulation. run() checks level OK, sends once, and verifies R.
    # Run also activates the pump. Reported state does not prove actual flow.
    chiller.run()
    print("Verified control loop: R (running). Check circulation at the equipment.")

    # 3. Perform your experiment, then explicitly Stop. Pump control is separate.
    input("Press Enter when ready to stop regulation. ")
    chiller.stop()
    print("Verified control loop: S (stopped)")
    print(f"Reported pump: {chiller.query('PMP')}")

    # 4. Pump-only operation requires loop S and coolant level OK.
    # set_pump() rejects changes while the control loop is running.
    input("Press Enter to turn on the pump with regulation stopped. ")
    chiller.set_pump(True)
    print("Verified pump state: ON. Check actual circulation at the equipment.")

    # 5. Turn the pump off as a separate, verified action.
    input("Press Enter when ready to turn off the pump. ")
    chiller.set_pump(False)
    print("Verified pump state: OFF")

# Exiting or Ctrl+C releases the port; it does not automatically Stop/Pump off.
