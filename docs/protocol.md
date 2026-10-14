# Protocol and command audit

Source: [NRC400 PC2 manual Rev 02](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2025-04/NRC400%20PC2%20User%20Manual%20Rv%2002.pdf),
part 387009443, 2025-02-28, sections 16.1–16.2, printed page 45.
All twelve codes were compared with the extracted table: no missing/extra codes.
[Rev 00, page 37](https://tark-solutions.com/sites/default/files/ckfinder/files/resources/Manuals/Nextreme-NRC400/NRC400-User%20Manual-Rev00.pdf)
has the same table; neither provides a complete wire transcript.

RS-232, rear DB-9, **115200 baud, 8N1, no flow control**. Queries are `$PC` plus
the code, e.g. `$PCCST`; writes append comma/data, e.g. `$PCTSP,20.0`. Omitting
visual spacing follows the query examples. No address/checksum is documented.

| Code | Meaning | Public API write / response |
|---|---|---|
| MOD | Model | NRC400 required |
| SSN | Serial number | Raw identity |
| VER | Firmware | Raw identity |
| CST | Supply temperature | Numeric C/F converted to Celsius |
| TSP | Target | One-decimal numeric write, verify readback |
| CTL | Control loop | R/S write and readback |
| PMP | Pump | ON/OFF write and readback, loop stopped |
| LVL | Level | LOW/OK; LOW blocks Run and Pump on |
| SCF | Display units | C/F write and readback |
| COL | Coolant percentage | Raw query; configure on panel |
| DAT | Date | Raw query; configure on panel |
| TIM | Time | HH:MM:SS write; validate clock advancement/readback |

The example CST response is `25.38 C`. TSP accepts bare numbers or numbers with
C/F, interpreting bare values in the SCF unit, and the captured
`Coolant Supply Temp Setpoint 12.000000 C` form. SCF accepts C/F and the captured
`Temperature in degrees C/F` form. CTL accepts R/S, Stopped/Running, and an anchored
`Running... IDENTIFIER` form (one ASCII letter followed by letters, digits or
underscores). Firmware 1.0.21 reports internal substates such as
`CheckCoolantSupplyTemperature`; a name whitelist proved incomplete. These are
reported controller states, not proof of physical cooling or flow. Unknown
headers/status words/unit spellings are rejected. High-level coolant
and date writes are omitted because full encoding/date order is unspecified;
the low-level transport can frame these writes. Alarm lists/clear, boost, offsets,
alarm configuration, flow, return temperature and PID settings have no documented
commands. Use the front panel; LVL does not establish a complete alarm state.

## Hardware assumptions

| Unspecified detail | Prototype / commissioning action |
|---|---|
| Command ending | CRLF default; configurable CR/LF/CRLF; capture actual bytes |
| Reply ending | CR or LF required; confirm framing |
| Echo | Ignore exact command echo only |
| Write reply | Required by default; explicit `write_response=false` observes optional replies and still rejects errors/partial frames; API readback is mandatory |
| Optional-reply window | 0.1 s default, configurable 0–5 s; COM7 profile uses 0.25 s with 2 s query timeout; verify per firmware/command |
| DB-9 wiring | Obtain vendor cable/pinout; no straight-through/null-modem guess |
| TSP units | Verify SCF semantics, particularly Fahrenheit |
| Persistence | Verify front-panel and power-cycle results |
| CTL,S pump effect | Query PMP and observe flow; no pump-off assumption |

The simulator's CRLF/OK replies are fixtures, not observed hardware behavior.
COM7 hardware testing on 2026-10-14 observed CRLF query TX / LF reply RX, no echoes,
and silent TSP/PMP writes followed by successful readback. The +1 C target change
confirmed that a silent write can apply on this unit. No timeout-based fallback
or automatic write replay is used. Details and physical limits are in
[the hardware session](hardware-session-2026-10-14.md).
The manual also conflicts: page 13 specifies firmware-dependent -5–40/50 °C;
page 45 lists TSP -10–40. Page 38 lists water 10–50, 10% EG 5–50, 20% EG 0–50,
30% EG -5–50. Default software limits are 10–40; configured limits must lie within
-5–50 and match actual firmware/coolant. Software cannot verify tank contents;
manage condensation when cooling below the application's dew point.

## Transactions and evidence

Every public setting command requires its own status readback. An acknowledgement
confirms only that a reply arrived; it is never the setting's success criterion.

| API method | Write | Required readback / acceptance |
|---|---|---|
| `set_setpoint(celsius)` | TSP, rounded to one device decimal | SCF + TSP; same units, transmitted target within 0.001 °C after conversion |
| `run()` | CTL,R | CTL normalizes to R; LVL=OK checked before writing |
| `stop()` | CTL,S | CTL normalizes to S; bounded query polling while R, no write replay |
| `set_pump(on)` | PMP,ON/OFF | PMP equals requested state; CTL=S checked first, LVL=OK before ON |
| `set_units(unit)` | SCF,C/F | SCF equals requested C/F |
| `set_time(time)` | TIM,HH:MM:SS | Valid clock readback; measured transaction time plus one tick, including midnight |

COL and DAT have no public setters; configure them on the panel. Direct
`SerialTransport.request(code, data)` is a low-level framing operation: it does
**not** verify setting readback or apply controller guards. Use the methods above
for normal operation. Queries require replies, including when write replies are
configured optional.

A failed/interrupted write or its readback blocks further API writes until explicit
close/reconnect. Diagnostic queries remain possible if the transport is healthy.
Inspect the panel first because the uncertain write may have applied. Reconnect
identifies the unit and resets the write guard; it sends no control command.

Clear buffered input, send one command, consume one nonempty reply after exact
echoes. Reply limit: 512 bytes. Timeout, non-ASCII, device error or partial write
invalidates the session; close/reconnect explicitly. There are no write retries.
Stop sends CTL,S once and polls readback while R (bare or descriptive), starting
no extra poll after its deadline; each serial request has its own timeout.
It never repeats the write. After a dashboard
fault, operator Stop/Pump off can explicitly reconnect and reidentify the bound
unit for deactivation; automatic polling and new activation remain locked.
Recovery also requires the original session serial number when no explicit
binding is configured. A replacement unit is closed without a control write.
The GUI reads a complete sample after the method's own readback and checks that
the affected field still matches. Drift or failure locks the session without replay.
Rejected input performs no write and still obtains fresh telemetry. CSV failure
locks recording/control but preserves the successfully queried state on screen.
Writes have separate readback queries; acceptance is not thermal settling,
calibration, capacity or circulation proof. Time readback permits measured
transaction time plus a clock tick, including midnight rollover.
Observed clock replies leave hours, minutes or seconds unpadded (e.g. `1:7:41`);
validated readback is normalized to HH:MM:SS. The device date/time was not calibrated.

Without transaction IDs, clearing buffers cannot eliminate every late-reply race.
Use one owner and measured timeouts/delays; investigate a failed write on the panel
because it may have applied. Retain verbose TX/RX transcripts (which include serial
identity). Independent literal-transcript tests check every query/write sequence
without using the simulator/encoder to generate expected command bodies.
