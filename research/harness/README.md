# jigbench — the research harness

`jigbench` runs the experiments in `research/PROTOCOL.md` against the **real** Jig runtime and **real** local
models. Nothing is mocked: every trial drives Jig's real gate, Sentinel, memory and agent loop, and every
number in the paper comes from `research/results/`. What changed from the protocol, and why, is in
`research/DEVIATIONS.md`.

## Set-up

The harness lives in its own virtual environment, `research/.venv`, with Jig installed from this
repository and `jigbench` installed from `research/harness/`. Commands below run from `research/`:

```powershell
.venv\Scripts\python -m jigbench --help
```

Models are listed in `research/models/models.toml`, with hashes, licences and quantisation in
`models.lock.json` (`models/fetch_models.py` downloads and verifies them). Datasets go in `research/data/`.

## Running

| Command | What it does |
|---|---|
| `python -m jigbench run <config> --policy overnight` | Run or resume one config under the compute policy. |
| `python -m jigbench run <config> --policy pilot --limit N` | A short development run that ignores the policy. Use it only when you are watching. |
| `python -m jigbench overnight --queue harness/configs/queue.yaml` | Work through the queue under the compute policy (what the scheduled task runs). |
| `python -m jigbench policy-status` | Show whether the policy would allow a start now, and why. |
| `python -m jigbench report [run dirs]` | Write tables and figures for the paper to `paper/generated/`. |
| `python -m jigbench handover-status` | Show unrestored GPU handovers and whether 8080 is idle. |
| `python -m jigbench submit-results <run dir> --contributor NAME` | Validate and package a run for contribution (see `CONTRIBUTING-RESULTS.md`). |

Runs are **resumable**. A trial counts as done only when its record has `status_harness: "ok"`. Errored
trials are retried on the next start, and a trial that is interrupted leaves no record.

## Configs

Configs live in `harness/configs/` (`pilot/` for small labelled pilots, `full/` for protocol runs). Each one
names a run (`name`), an experiment (`d1` safety on AgentDojo, `c1` memory on LongMemEval), and sets out:
- `endpoints`: either an existing server (`base_url`, such as the owner's 8080) or a harness-started
  llama.cpp server (`server: {model, port, ctx, parallel, n_cpu_moe}`, ports 8090–8099);
- `agent`, `judge` and `conditions`: which endpoint plays which role;
- `sampling` and `seeds`;
- experiment-specific settings such as `suites`/`attacks` for D1, or `strategies`/`summary_word_limit` for C1.

A config with `closed: <reason>` is an earlier run that must not be resumed: the runner refuses it.
`tests/test_configs.py` checks that every model a config names is in the catalogue.

Current protocol runs (v0.4):

| Config | Run | Notes |
|---|---|---|
| `full/d1_full.yaml` | `d1-full-v1` | **Closed** earlier run (subsample, seed 0, before the bare-URL fix). |
| `full/d1_full_v2.yaml` | `d1-full-v2` | Whole suites, seeds 0–2, Jig's default sampling sent explicitly. |
| `full/d1_full_v2_qwen.yaml` | `d1-full-v2-qwen` | The Qwen reviewer condition (refused by the VRAM guard while 8080 is up). |
| `full/c1_full.yaml` | `c1-full-v1` | Four memory strategies, 30 questions. |
| `full/c1_full_summary.yaml` | `c1-full-v1-summary-nolimit` | Rolling summary without a word limit. |

## Queue and scheduled tasks

`harness/configs/queue.yaml` lists configs in priority order. `overnight` takes each in turn and resumes it.
If an entry cannot start (for example, the VRAM guard refuses its server), that entry is logged as an error
and the queue moves on to the next one. A finished entry is skipped in seconds.

Two Windows scheduled tasks, both per-user, run only while the user is logged on and never wake the
computer:
- `\Jig\Jig Research Overnight` runs `scripts/overnight.ps1` every 30 minutes. The Python side decides
  whether to run (`scripts/register-overnight-task.ps1` registers it; `-Disable`/`-Remove` stop it).
- `\Jig\Jig Research Restore` runs `scripts/restore-watchdog.ps1` every 5 minutes (see Handover).

Only one `overnight` process runs at a time (lock file `logs/overnight.lock`). Don't start a manual `run`
while it is active.

## Compute policy

A run starts only:
- between 01:00 and 07:00 Europe/London after 5 minutes without input, or at any time after 30 minutes
  without input; and
- if the GPU averages at most 25% use over a 5-second sample.

While a trial runs, the harness checks for user input every 5 seconds. As soon as the user is active it
cancels the trial, stops its model servers and exits. It also re-checks GPU use between trials. A harness
server is refused if starting it would leave less than 2 GiB of VRAM free: it fails loudly and never falls
back to anything smaller. The owner's 8080 server is used only through its API.

## Handover of the 8080 server

`jigbench.handover` implements PROTOCOL §9a: a safe, reversible stop of the 8080 server for runs that need
the whole GPU. It checks all of these immediately before stopping anything:
- it is inside the overnight window, or the user has been idle for 30 minutes or more;
- 8080 has had no request for 10 minutes;
- Jig is idle with nothing scheduled;
- no pytest, recording or Playwright process is running.

It pauses Jig, records the exact command line, working directory and environment, and always restores
8080 afterwards (in `try/finally`, and through the independent restore watchdog), then unpauses Jig. The
handover is not yet wired into the runner, so no current run stops 8080.

## Results

Each run writes `research/results/<experiment>/<run name>/`:
- `run.json`: provenance for each session. This covers the Jig commit and dirty state, the hardware, and
  for every endpoint the served model file, quantisation, llama.cpp build, context, slots and default
  sampling, plus the full config.
- `trials.jsonl`: one record per finished or errored trial. D1 records hold AgentDojo's raw `utility` and
  `security`, `attack_success`, blocks by type and source, tool errors, approvals and the agent's request
  sampling. C1 records hold the judged answer, token counts and wall time.
- `raw/`: Jig data directories when `keep_raw` is set (gitignored).

`report` recomputes derived fields from the raw ones (for example, D1 attack success from `security`). It
treats an errored trial that was later retried as superseded, and tags a run INTERIM until all its planned
trials are done. It writes `paper/generated/<exp>-<run>.tex/.pdf/.png` and `results-index.tex`. Statistics
(Wilson intervals, bootstrap means) are in `jigbench.stats`.

Logs are written to `research/logs/` (gitignored): per-command logs, server logs in `logs/servers/`, and
handover records.

## Tests

```powershell
cd research\harness
..\.venv\Scripts\python -m pytest -q
```

The tests use real components: Jig's real gate over real AgentDojo tools and environments, real config
loading and planning, a real local HTTP server where a test needs to see what goes over the wire, and real
model servers where marked. A few tests skip unless their resources are present:
- `test_c1_cutoff.py` needs `JIGBENCH_LIVE_8080=1` and a running 8080;
- `test_overnight.py` needs the verified granite 4.2 8B download and a free port 8097.
