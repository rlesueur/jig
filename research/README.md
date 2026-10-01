# Jig research programme: always-on local agents

Author: Robyn Le Sueur. Licence: Apache-2.0 (code); benchmark pages contain Wikipedia text under CC BY-SA 4.0.

This directory holds a living research programme and paper on two questions for always-on local agents,
using [Jig](../README.md) as the testbed:

* **C — memory and context over days:** summarisation, retrieval, consolidation, forgetting, staleness.
* **D — safety when unattended:** action reviewers (the Sentinel), indirect prompt injection, poisoned
  memories that persist (C∩D), data exfiltration, and approval burden.

Nothing here modifies Jig's core. The harness drives the real Jig runtime, real local models and real
local services; benchmark scenarios are labelled as such.

| Path | What it is |
|---|---|
| `literature/` | Verified literature review (`review.md`), BibTeX (`references.bib`), citation checker |
| `PROTOCOL.md` | Hypotheses, metrics, conditions, statistics, threats to validity, ethics |
| `benchmark/` | Local benchmark web site (8 Wikipedia extracts + labelled injection variants) and request sink |
| `harness/` | `jigbench`: runner, experiments C1/D1/D2/CD1, report, results schema, tests |
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
.\.venv\Scripts\python -m jigbench run harness\configs\pilot\d1_pilot.yaml --policy pilot
.\.venv\Scripts\python -m jigbench report                               # tables + figures
```

Docker Desktop must be running (the benchmark site and sink are containers on 127.0.0.1:8098/8099).
Model servers started by the harness use ports 8090–8097 only; port 8080 is never started or stopped.

## Commands

* `jigbench run CONFIG [--policy overnight|pilot] [--limit N]` — run or resume an experiment. With the
  default `overnight` policy it only starts between 01:00 and 07:00 Europe/London (or after 30 minutes of
  idle), and stops promptly if you return or another process needs the GPU.
* `jigbench report [RUN_DIR ...]` — summary tables (Markdown and LaTeX) and figures.
* `jigbench submit-results RUN_DIR --contributor HANDLE` — validate and package results; see
  [CONTRIBUTING-RESULTS.md](CONTRIBUTING-RESULTS.md).
* `jigbench overnight --queue harness\configs\queue.yaml` — what the scheduled task runs.
* `jigbench policy-status` — what the compute policy would decide right now.

## Overnight task

`scripts\register-overnight-task.ps1` registers the per-user task `\Jig\Jig Research Overnight`
(no admin rights). It wakes every 30 minutes and exits at once unless the compute policy allows a run.
Logs: `research\logs\`. To stop it: `scripts\register-overnight-task.ps1 -Disable`.

## Citing

See `CITATION.cff`.
