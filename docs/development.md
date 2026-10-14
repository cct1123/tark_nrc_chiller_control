# Code map and maintenance

The package has eight focused Python modules. Read the code in this order:

| File | Responsibility |
|---|---|
| `transport.py` | Serial settings, single-command ASCII framing, reply validation, transport fault guard |
| `controller.py` | Identity, Celsius conversion, device guards, one-write/verified-readback operations |
| `config.py` | Build an unopened controller from TOML and explicit overrides |
| `recording.py` | Exclusive CSV creation and flushed sample/error rows |
| `gui.py` | One serialized dashboard session, state-based controls, target synchronization and history |
| `__main__.py` | CLI parser, explicit command dispatch, connection/CSV ownership and logging loop |
| `simulator.py` | Explicit offline peer used by CLI demos and software regression tests |
| `__init__.py` | Public imports; opens no port and starts no background work |

The CLI constructs the controller, connects it inside an `ExitStack`, then passes
that same owner to a command or GUI. There is no second GUI driver or background
serial service. CSV and connection cleanup share the stack. Hardware selection
never falls back to simulation. One operator and one serial owner are supported.

## Write and GUI flow

Input validation and device prerequisites precede a write. The controller's
`_verified_write` block sends once and encloses the required query/comparison.
Any exception after transmission, including interruption, marks the write outcome
uncertain and blocks subsequent writes until explicit reconnect. Invalid input
before transmission does not create this fault. See the [verification table](protocol.md#transactions-and-evidence).

`DashboardSession.refresh` serializes an optional action, a fresh full sample,
verification against that sample, history and CSV. The layout callback renders
that result. Explicit controls take priority over coalesced polling ticks. If
multiple controls arrive together, Stop/Pump off take priority; conflicting
activation requests are rejected without writes. Button availability comes from current reported state rather than
click counters. A rejected action still queries fresh state. A successful query
remains displayable if CSV fails; the fault notice makes clear that recording and
activation are locked.

The browser's `reported-target` store holds only the previous readback for comparing
the target input with an unedited value. Polling follows external target changes
when that value is unchanged, preserves a draft otherwise, and uses verified
readback after Apply. It is not an authoritative device cache. Communication
failure blanks current telemetry; historical plot points remain historical.

After a GUI fault, explicit operator Stop/Pump off can reconnect/reidentify and
verify the requested deactivation once. The failed session remains locked and
requires restart before activation. Never replay an uncertain previous command.
Recovery checks the original session's serial number even without a configured
binding; a replacement unit is disconnected before any control write. Combined
button notices retain any action rejection reason.
If communication prevents verification, use the front panel.

## Checks and changes

Use the virtual environment and run `python -m pytest`,
`python -m ruff check src tests examples`, and
`python -m ruff format --check src tests examples`.
Literal transcripts independently check wire commands; simulator cases check
workflows; Dash HTTP dispatch tests check rendered state and control guards.
None of these is physical hardware acceptance.

Keep public write readback tests when changing control code. Add regressions for
observed faults: wrong readback, interrupted writes, status drift after an action,
panel target changes, rejected input and CSV errors. Keep GUI imports lazy so the
API/CLI can be used without dashboard dependencies. Build with `python -m build`
and check packaged CSS plus current source/documentation.

Historical scripts and traces under `outputs/` are session evidence, not maintained
runtime code. Preserve them, name new result files exclusively, and label offline
checks separately from instrument measurements. The 2026-10-14 bench report applies
to the software tested at that time; later refactors require their own validation.
