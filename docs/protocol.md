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
C/F, interpreting bare values in the SCF unit. Enum replies must match the table;
unconfirmed headers/status words/unit spellings are rejected. High-level coolant
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
| Write reply | One line expected; verify before choosing `write_response=false` |
| No-reply delay | 0.1 s default, configurable 0–5 s; measure processing latency |
| DB-9 wiring | Obtain vendor cable/pinout; no straight-through/null-modem guess |
| TSP units | Verify SCF semantics, particularly Fahrenheit |
| Persistence | Verify front-panel and power-cycle results |
| CTL,S pump effect | Query PMP and observe flow; no pump-off assumption |

The simulator's CRLF/OK replies are fixtures, not observed hardware behavior.
The manual also conflicts: page 13 specifies firmware-dependent -5–40/50 °C;
page 45 lists TSP -10–40. Page 38 lists water 10–50, 10% EG 5–50, 20% EG 0–50,
30% EG -5–50. Default software limits are 10–40; configured limits must lie within
-5–50 and match actual firmware/coolant. Software cannot verify tank contents;
manage condensation when cooling below the application's dew point.

## Transactions and evidence

Clear buffered input, send one command, consume one nonempty reply after exact
echoes. Reply limit: 512 bytes. Timeout, non-ASCII, device error or partial write
invalidates the session; close/reconnect explicitly. There are no retries.
Writes have separate readback queries; acceptance is not thermal settling,
calibration, capacity or circulation proof. Time readback permits measured
transaction time plus a clock tick, including midnight rollover.

Without transaction IDs, clearing buffers cannot eliminate every late-reply race.
Use one owner and measured timeouts/delays; investigate a failed write on the panel
because it may have applied. Retain verbose TX/RX transcripts (which include serial
identity). Independent literal-transcript tests check every query/write sequence
without using the simulator/encoder to generate expected command bodies.
