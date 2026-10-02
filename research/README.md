# Jig research programme: always-on local agents

Author: Robyn Le Sueur. Licence: Apache-2.0 (code).

This directory holds a living research programme and paper on two questions for always-on local agents,
using [Jig](../README.md) as the testbed:

* **C — memory and context over days:** summarisation, retrieval, consolidation, forgetting, staleness.
* **D — safety when unattended:** how well Jig's defence layers (core and custom rules, the Sentinel under
  several model choices, read-only research mode, and the approval queue) reduce the attack success rate of
  **published** prompt-injection benchmarks, and at what cost to utility, false positives and approvals.

This programme authors **no** attack content. Safety is measured only with published benchmark cases run
unchanged. Nothing here modifies Jig's core. The harness drives the real Jig runtime, real local models
and real local services.

| Path | What it is |
|---|---|
| `literature/` | Verified literature review (`review.md`), BibTeX (`references.bib`), citation checker |
| `PROTOCOL.md` | Hypotheses, metrics, conditions, statistics, threats to validity, ethics |
| `harness/` | `jigbench`: runner, the memory experiment (C1), the published-benchmark safety adapter (D1), report, results schema, tests |
| `models/` | Model manifest and checksum-verifying downloader |
| `scripts/` | Overnight scheduled-task wrapper and registration |
| `results/` | Per-run provenance and summaries (raw trial dumps are gitignored) |
| `paper/` | LaTeX paper; `paper/generated/` is written by `jigbench report` |

## Quick start

```powershell
cd research
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e ..[dev] -e harness huggingface_hub matplotlib pyyaml psutil scipy
.\.venv\Scripts\python models\fetch_models.py --only granite42-8b      # verified download
.\.venv\Scripts\python -m pytest harness                                # harness tests
.\.venv\Scripts\python -m jigbench run harness\configs\pilot\c1_pilot.yaml --policy pilot
.\.venv\Scripts\python -m jigbench report                               # tables + figures
```

Model servers started by the harness use ports 8090–8099 only. The main server on port 8080 is stopped and
restarted only through the safe, reversible GPU handover described below.

## Commands

* `jigbench run CONFIG [--policy overnight|pilot] [--limit N]` — run or resume an experiment. With the
  default `overnight` policy it only starts between 01:00 and 07:00 Europe/London (or after 30 minutes of
  idle), and stops promptly if you return or another process needs the GPU.
* `jigbench report [RUN_DIR ...]` — summary tables (Markdown and LaTeX) and figures.
* `jigbench submit-results RUN_DIR --contributor HANDLE` — validate and package results; see
  [CONTRIBUTING-RESULTS.md](CONTRIBUTING-RESULTS.md).
* `jigbench overnight --queue harness\configs\queue.yaml` — what the scheduled task runs.
* `jigbench policy-status` — what the compute policy would decide right now.
* `jigbench handover-status` — pending (unrestored) GPU handovers, 8080 idleness and Jig's state.
* `jigbench restore-watchdog` — restore the main server from the latest unrestored handover if no harness
  is alive (what the restore watchdog task runs).

## Overnight task

`scripts\register-overnight-task.ps1` registers the per-user task `\Jig\Jig Research Overnight`
(no admin rights). It wakes every 30 minutes and exits at once unless the compute policy allows a run.
Logs: `research\logs\`. To stop it: `scripts\register-overnight-task.ps1 -Disable`.

## Safe GPU handover and the restore watchdog

Some runs need a test model to have the whole GPU, so the harness briefly stops the main model server on
port 8080 and **always restores it** (`jigbench.handover`; see PROTOCOL.md §9a). Before stopping anything
it re-checks that it is in the overnight window or the user is idle, that 8080 has had no request for ten
continuous minutes (via `/slots` or `/metrics`), that Jig reports no running or scheduled work, and that no
`pytest`/demo/`playwright` process is running. It pauses Jig, records the exact command line, working
directory and environment of the 8080 process to `research\logs\handover-<ts>.json`, stops it gracefully,
runs the test model, then restarts the original identically, waits for `/v1/models` to serve the same
alias, and unpauses Jig. If Jig itself supervises 8080 (via `[model.launch]`), the handover uses Jig's power
API instead of killing the process, and refuses if that API is not yet available.

Restoration is guaranteed twice: by `try/finally` in the harness, and by an independent watchdog task
`\Jig\Jig Research Restore` (every 5 minutes) that restores from the latest unrestored handover file when no
harness is alive. Register it with `scripts\register-restore-task.ps1` (no admin); disable it with
`scripts\register-restore-task.ps1 -Disable`. Every handover step is logged to `research\logs\handover.log`.

## Citing

See `CITATION.cff`.
