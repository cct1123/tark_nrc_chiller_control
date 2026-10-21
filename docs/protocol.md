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
| CTL | Control loop | R/S writes; R/S/QUIT in samples; Stop accepts S/QUIT |
| PMP | Pump | ON/OFF write and readback; no loop prerequisite |
| LVL | Level | LOW/OK telemetry; no application control prerequisite |
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

On 2026-10-21 the operator reported the unit already stopped, then supplied a
Stop failure showing persistent `CTL=QUIT` on firmware 1.0.21. The manual's CTL
table specifies only R/Run and S/Stop. Based on that observation, Stop now accepts
S/Stopped or QUIT as terminal replies, while Run still requires R/Running. A
running Stop transition is polled until S/QUIT or the deadline, without repeating
the write. Samples retain QUIT verbatim after case normalization; it is not
globally aliased to S. The dashboard and tutorial apply the same Stop acceptance.
Other unknown CTL replies still fail validation. This is an operator-reported
firmware observation with offline regression coverage, not independent physical
validation of stopping, pump-off or other firmware revisions.

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
30% EG -5–50. The application imposes no target window; select targets for the
actual firmware, coolant and experiment. Software cannot verify tank contents.
These are vendor operating specifications, not application-enforced limits.

## Transactions and evidence

Every public setting command requires its own status readback. An acknowledgement
confirms only that a reply arrived; it is never the setting's success criterion.

| API method | Write | Required readback / acceptance |
|---|---|---|
| `set_setpoint(celsius)` | TSP, rounded to one device decimal | SCF + TSP; same units, transmitted target within 0.001 °C after conversion |
| `run()` | CTL,R | CTL normalizes to R; no level prerequisite |
| `stop()` | CTL,S | CTL is S or QUIT; bounded query polling while R, no write replay |
| `set_pump(on)` | PMP,ON/OFF | PMP equals requested state; no loop/level prerequisite |
| `set_units(unit)` | SCF,C/F | SCF equals requested C/F |
| `set_time(time)` | TIM,HH:MM:SS | Valid clock readback; measured transaction time plus one tick, including midnight |

COL and DAT have no public setters; configure them on the panel. Direct
`SerialTransport.request(code, data)` is a low-level framing operation: it does
**not** verify setting readback. Use the methods above
for normal operation. Queries require replies, including when write replies are
configured optional.

Every connected user can issue control commands. There is no read-only flag,
software setpoint window, level/loop prerequisite or permanent write-fault latch.
An error or Ctrl+C may leave a command's outcome uncertain; the error is reported,
no command is automatically replayed, and the next explicit user action is allowed.

Clear buffered input, send one command, consume one nonempty reply after exact
echoes. Reply limit: 512 bytes. Timeout, non-ASCII or partial/oversized frames make
the link unusable until close/reconnect. A complete device rejection raises an
error but leaves the connection usable. The GUI closes a broken link and offers
Connect again. Reconnection verifies unit identity and sends no control command.
The GUI reads a complete sample after a setting's own readback and reports drift
without locking controls. Rejected input still obtains fresh telemetry. CSV errors
stop recording and retain telemetry while controls remain available. Run/Stop and
pump readback confirms reported state, not physical flow or thermal performance.
Stop is sent once; R readback is polled within a bounded deadline until S/QUIT. Each serial
request has its own timeout. Time readback allows measured elapsed time plus one
tick, including midnight rollover.
Observed clock replies leave hours, minutes or seconds unpadded (e.g. `1:7:41`);
validated readback is normalized to HH:MM:SS. The device date/time was not calibrated.

Without transaction IDs, clearing buffers cannot eliminate every late-reply race.
Use one owner and measured timeouts/delays; investigate a failed write on the panel
because it may have applied. Retain verbose TX/RX transcripts (which include serial
identity). Independent literal-transcript tests check every query/write sequence
without using the simulator/encoder to generate expected command bodies.
