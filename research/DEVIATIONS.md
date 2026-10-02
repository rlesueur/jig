# Deviations from PROTOCOL.md

Every change to how an experiment is run or scored, relative to the protocol version in force, is recorded
here with its date, reason and effect on results. Pilots are labelled as such and are not covered by the
protocol's inferential plan.

| Date (UK) | Protocol | Experiment | Deviation | Reason | Effect on results |
|---|---|---|---|---|---|
| 2026-10-02 02:40 | v0.3 | D1 | "Blocked calls" counts only gate refusals (`ModeViolation`, `PolicyBlocked`, `ApprovalDenied`); tool errors after the gate allowed a call are reported separately as `tool_errors`; a reviewer failure (`SentinelError`) makes the trial a harness error (retried) instead of a block. | Jig fails closed on a reviewer error and the adapter counted every failed outcome as blocked, which would have credited the defences with crashes and malformed calls. | Clarifies the §2 metric before any D1 trial ran; no D1 results existed. |
