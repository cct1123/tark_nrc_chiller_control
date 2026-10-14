# Physical commissioning

Follow the full [manufacturer manual](sources/README.md) for the fitted unit.
Partial hardware checks were performed on 2026-10-14; see
[the recorded results and limits](hardware-session-2026-10-14.md). Complete
physical acceptance requires the remaining evidence below.

## Installation

Use a grounded mains connection/power cord, certified PC equipment, a genuine
RS-232 adapter supporting 115200 8N1 and a vendor-confirmed DB-9 cable. RS-485/TTL
are incompatible. Obtain the pinout and straight-through/null-modem requirement;
the manual does not establish these. See electrical requirements in sections 3/10.

Match coolant, hose materials and mating connectors to the application/part number;
pages 22–24 distinguish standard/special connectors. Keep the unit level, meet
Figure 5's airflow clearance (p21), leave ports/power accessible and avoid hose bends.
Operating ambient: 15–40 °C, 35–85% RH, altitude below 2000 m (p13). Nominal 400 W
is at 20 °C coolant/ambient; use the performance graph for other operating points.
Fill, prime, check leaks/flow and select the actual coolant on the panel (pp25–28).
Never run the pump dry. The return/inlet limit is 60 °C (p9); serial CST measures
supply only. Provide suitable independent heat-source thermal/flow protection.

## Read-only connection

1. Record model/part number, serial/firmware, coolant, cable/pinout, adapter and COM
   port. Disable heat load initially and close competing serial programs.
2. In a terminal at 115200 8N1/no flow control, query MOD, VER and CST. Capture
   command/reply endings and latency; finish outstanding requests before changing endings.
3. Enter verified framing/timeout in `config.toml`; keep writes disabled. Run
   `--config config.toml --verbose identify`, then `read`.
4. Compare all identity, temperature/target, units, pump/loop, level and coolant
   readings with the panel. Record ten minutes of CSV; check timestamps/values/gaps.
5. If replies fail, close and resolve wiring/framing/format before reconnecting.
   Hardware selection never substitutes simulation.

## Controlled writes

Enable writes only with verified communication and a filled/primed circuit;
supervise the first actions at the panel.

1. Try a modest approved target change, e.g. 20 to 21 °C. Capture write reply and
   queried readback; establish acknowledgement presence and no-reply delay before
   configuring `write_response`. Check front-panel agreement and power-cycle persistence.
2. With the loop stopped, test pump on/off and observe flow. Run the loop; compare
   CTL/PMP/panel state and physical coolant settling under the planned load.
3. Stop the loop; record pump/fan behavior. Use separate pump-off when appropriate
   for the experiment. Confirm C/F behavior without changing the physical target.
4. Disconnect the serial link while supervised; record retained operation/recovery.
   Reconnect read-only; no command should be replayed.
5. Use vendor-permitted procedures for alarms/interlocks. Do not create faults by
   starving/blocking the pump. Validate independent return-temperature/flow protection.

## Acceptance record

Attach raw serial transcripts/CSV and record operator/date plus evidence for:
unit identity/firmware; coolant/panel setting/target limits; plumbing/prime/leaks/flow/
clearance; mains/ground/adapter/cable; framing/latency/timeout/write reply/delay;
panel comparisons; target persistence/C/F; pump/loop/fan behavior; calibration/
settling/load-ambient capacity; independent protection; alarms/link-loss recovery.
Mark each item separately when documented; the session report identifies the
completed communication/control checks and remaining physical limits. Software
tests are not bench acceptance.
