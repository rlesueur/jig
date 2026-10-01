# Contributing results to the living benchmark

The benchmark is meant to grow with results from other people's hardware and models. A contribution is a
folder under `research/submissions/` produced by `jigbench submit-results`, sent as a pull request.

## Rules

1. **Real runs only.** Results must come from `jigbench run` against real model servers. Do not edit
   `trials.jsonl` by hand, and do not substitute generated or simulated model outputs.
2. **Unmodified scenarios.** Use the payloads in `harness/scenarios/payloads.yaml` and the pages in
   `benchmark/site/` as committed. If you change them, name the run accordingly and say so in `--notes`;
   such runs are reported separately.
3. **Local targets only.** The harness refuses every host except the reserved `*.jigbench.test` names,
   which it routes to your own local containers. Never point payloads at real third-party services.
4. **Provenance is required.** `run.json` must contain the model files (path, size, quantisation), the
   server build, the hardware and the Jig commit. The harness records these automatically; submissions
   without them are rejected by the schema check.
5. **Keep personal data out.** Trial records contain the benchmark canary (a fictitious account number),
   not your data. Check `--notes` and the run name before submitting.

## Steps

```powershell
.\.venv\Scripts\python -m jigbench run harness\configs\full\d1_full.yaml        # or your own config
.\.venv\Scripts\python -m jigbench submit-results results\d1\<run-name> --contributor <handle> --notes "RTX 4090, Windows 11"
git checkout -b results/<handle>-<run-name>
git add research/submissions/<folder>
git commit -m "Results: <experiment> on <models> (<hardware>)"
```

Then open a pull request. `submit-results` validates the files against the results schema
(`harness/jigbench/schema.py`, version 1) and writes:

* `manifest.json` — schema version, contributor, counts and the SHA-256 of the trial records;
* `run.json` — provenance for every session of the run;
* `summary.md`, `summary.json` — the same tables the paper uses;
* `trials.jsonl.gz` — the trial records (attach it to the pull request rather than committing it if it is
  larger than a few megabytes).

## Results schema (version 1)

Every trial record has `trial_id`, `condition`, `seed`, `status_harness` (`ok` or `error`),
`started_at`, `duration_s` and `endpoint_models`. Experiment-specific required fields:

| Experiment | Required fields |
|---|---|
| D1 (reviewer at the gate) | `label`, `tool`, `decision`, `caught`, `correct` |
| D2 (indirect injection) | `payload`, `channel`, `attempted`, `leaked`, `utility`, `approvals` |
| CD1 (memory poisoning) | `payload`, `poison_written`, `poison_retrieved`, `attack_success`, `leaked` |
| C1 (memory strategies) | `question_id`, `strategy`, `correct`, `total_prompt_tokens` |

Trials with `status_harness: error` are kept and counted, never silently dropped.
