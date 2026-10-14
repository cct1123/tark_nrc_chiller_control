# Validation

Windows / Python 3.12.7, 2026-10-14. The original checks below preceded hardware
connection. Actual API/CSV/GUI and target/pump serial checks have now been performed;
see [hardware results and physical limits](hardware-session-2026-10-14.md).
Physical acceptance in commissioning.md remains incomplete.

| Check | Result |
|---|---|
| Original suite against pruned runtime | 81 passed |
| Maintained test suite / Ruff after hardware fixes | 106 passed; lint clean |
| Subsequent synchronization/readability review | 135 passed; Ruff lint/format and dependency checks clean |
| CLI/API/UI equivalence | Exact CLI help/results, API wire bytes, layout/dependencies, six HTTP callbacks |
| Source archive/wheel / isolated wheel / dependencies | Built and smoke-tested; packaged CSS/controls passed; pip check clean |
| CLI/Python example, CSV | Identity/state returned; exclusive files, blank error measurements |
| Browser simulator before pruning | Setpoint, Run/Stop, pump off/on, 600-sample trend verified |

Tests cover all twelve query bodies, control/readback sequences, C/F conversion,
limits, read-only/LOW-level guards, pump sequencing, clock ticks/midnight, mismatch,
model/unit changes, echo/framing, truncated/non-ASCII/oversized/timeout/error replies,
partial writes/no replay, configuration, CLI, CSV integrity, dashboard and disk failure.

All original literal query/write expectations and 81 test cases were retained, with
regressions added for captured descriptive replies, optional-reply errors, GUI target
initialization/control guards, clock padding, exact unit binding, single-write
Stop transition polling and explicit deactivation recovery after a GUI fault. Transcript/
simulator tests do not prove hardware framing/replies. Clock readback now rejects
18:30 requested / 12:00 reported without replay; protocol.md records remaining gaps.

Dependencies: ../requirements-tested.txt. Tests/builds need normal Windows access
because the sandbox blocked Python-created temporary directories. Local evidence
`outputs/dashboard.jpg` and `outputs/dashboard-demo.csv` is labelled simulator,
not equipment measurement. No calibration, thermal, routing or link-loss bench proof.

The subsequent code review adds API write-fault latching, stricter Stop polling
deadlines, post-action sample checks, target synchronization after panel edits,
fresh telemetry after rejected input and preserved device readback on CSV failure.
These changes are validated offline; the earlier hardware session is retained as
historical evidence and does not establish bench acceptance of the refactor.
New review evidence is in `outputs/20261014-code-review-slim/`.
Combined timer/button HTTP tests reproduced five lost command cases before the
callback fix and now verify each control is sent exactly once. Conflicting
activation requests are rejected; simultaneous Stop takes priority without
implicitly switching off the pump.

The final pre-commit review reproduced and fixed hidden rejection notices for
combined controls and recovery accepting a replaced unit without a configured
serial binding. Two additional regressions cover those paths; the final suite
has 137 passing tests. Hardware was not actuated during this review.
