# Validation

Windows / Python 3.12.7, 2026-10-14. Hardware was not connected or actuated.
Physical acceptance in commissioning.md remains pending.

| Check | Result |
|---|---|
| Original suite against pruned runtime | 81 passed |
| Maintained test suite / Ruff | 81 passed (0.24 s); lint clean |
| CLI/API/UI equivalence | Exact CLI help/results, API wire bytes, layout/dependencies, six HTTP callbacks |
| Source archive/wheel / isolated wheel / dependencies | Built and smoke-tested; packaged CSS/controls passed; pip check clean |
| CLI/Python example, CSV | Identity/state returned; exclusive files, blank error measurements |
| Browser simulator before pruning | Setpoint, Run/Stop, pump off/on, 600-sample trend verified |

Tests cover all twelve query bodies, control/readback sequences, C/F conversion,
limits, read-only/LOW-level guards, pump sequencing, clock ticks/midnight, mismatch,
model/unit changes, echo/framing, truncated/non-ASCII/oversized/timeout/error replies,
partial writes/no replay, configuration, CLI, CSV integrity, dashboard and disk failure.

All literal query/write expectations and 81 test cases were retained. Transcript/
simulator tests do not prove hardware framing/replies. Clock readback now rejects
18:30 requested / 12:00 reported without replay; protocol.md records remaining gaps.

Dependencies: ../requirements-tested.txt. Tests/builds need normal Windows access
because the sandbox blocked Python-created temporary directories. Local evidence
`outputs/dashboard.jpg` and `outputs/dashboard-demo.csv` is labelled simulator,
not equipment measurement. No calibration, thermal, routing or link-loss bench proof.
