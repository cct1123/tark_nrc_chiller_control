# NRC400 hardware session — 2026-10-14

Windows / Python 3.12.7, actual COM7 USBG-232 (FT231X, 0403:6015,
D200MMC5A), NRC400 s26270058, TEA firmware 1.0.21. FTDI 2.12.36.20 reports OK.
Physical unplug/replug isolated COM7. COM3 (VDI) and COM4/COM6 (Windfreak)
were listed but never opened. Operator confirmed an RS-232 DB9 connection;
its exact cable topology/pinout was not independently measured.

## Software and serial results

- Ten complete reads of all twelve statuses passed before and after the parser fixes.
  The fixed API also produced ten complete CSV samples with hardware labels.
- Real CLI and GUI identified the unit and displayed live temperature, target,
  reported loop/pump, level and trend. The GUI initializes its target from readback,
  retains operator edits during polling, and disables unavailable controls.
- API target sequence 12 → 12 → 13 → 12 °C passed readback. GUI target
  12 → 13 → 12 °C also passed. Each write was sent once.
- PMP ON/OFF passed serial readback through the GUI and a longer operator-requested
  test (about 122 seconds ON). The operator subsequently confirmed actual circulation
  and successful manual Run/Stop testing; no independent flow measurement was recorded.
- Query TX was CRLF; observed reply RX was LF without echoes. TSP and PMP writes
  returned no acknowledgement in the captured observation windows. Explicit
  optional-reply mode with 0.25 s observation and 2 s query timeout is used in the
  tested local profile; it never switches modes automatically after a timeout.
- Software regression checks are separate from this bench evidence. They cover
  captured replies, strict unknown-value rejection, partial/error replies in optional
  acknowledgement mode, control guards, real Dash callback dispatch and identity binding.

Captured replies include `Temperature in degrees C`,
`Coolant Supply Temp Setpoint 12.000000 C`, `Stopped`, and unpadded clock fields
such as `1:7:41`. Captured CTL replies also included
`Running... WasStartControlLoopButtonPressedOff` and
`Running... MainLoopCheckCoolantSupplyLevelAndPressure`, and
`Running... CheckCoolantSupplyTemperature`. The parser accepts an anchored
Running identifier substate, rather than an incomplete name whitelist. Malformed
diagnostics remain rejected. These replies do not prove physical cooling.
No agent-generated CTL,R command was sent
before handoff for manual testing.

## Failures and physical limits

The original high-level parser rejected SCF, TSP and CTL descriptions. The original
GUI stopped at its first telemetry refresh and wrote an error-only CSV row.
These format issues were fixed and retested against the real unit.

The first unchanged-target write failed because the original default required an
acknowledgement. It was not replayed. Subsequent explicitly configured optional-reply
tests verified changed target values, rather than assuming that a timeout meant acceptance.
One browser wait expired during target restoration; raw serial evidence showed the
single write and confirmed 12 °C readback, without a replay.

The operator initially reported no circulation/no alarm, then was unsure about the
panel observation during the brief pump test. The longer test was provided for
observation. During manual Run, an unhandled Running identifier stopped GUI polling
and disabled Stop. This was repaired with the identifier grammar, bounded Stop
readback polling, and explicit operator deactivation after a fault. A separate
recovery connection found the unit already stopped/off before sending CTL,S and
PMP,OFF once each; those commands cannot be credited with the prior shutdown.
The operator then confirmed successful manual Run/Stop and circulation. Final
dashboard checks found R/ON at 14 °C despite the operator's safe-state confirmation;
the agent used Stop once, verified S/OFF, and restored the target to 12 °C. Final
physical panel confirmation is recorded separately in the local closeout evidence.

Serial telemetry does not establish actual flow, return temperature, complete alarms,
calibration, thermal settling/capacity, or independent protection. LVL=OK alone is
insufficient. Date `1/1/0` and elapsed-looking time are uncalibrated. Coolant code 0
does not verify physical tank contents. Closing the port retains physical state;
Stop does not imply pump-off.

## Evidence

Local immutable evidence is under
`outputs/20261014-150540-0500-nrc400-hardware/`; corrected-software traces and tests
are in `iteration-20261014-152818-0500/`. This includes raw per-byte TX/RX/timings,
CSV, screenshots, operator statements, original failures, setpoint readbacks,
extended pump results and ongoing manual-GUI logs. These files are excluded from
Git/distribution. The earlier no-edit evidence remains unchanged.

The local `config.toml` binds the exact SSN, limits setpoints to the approved 12–20 °C,
and defaults to writes disabled. Explicitly enabling writes requires supervised
physical readiness, not just successful serial reads.
