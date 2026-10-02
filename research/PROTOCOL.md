# Research protocol: optimising always-on local agents (memory and unattended safety)

Version 0.4, 2 October 2026. Living document. Author: Robyn Le Sueur. The testbed is Jig (Apache-2.0).
Every experiment runs against the **real** Jig runtime and **real** local models; no model output is
faked and no service is stubbed.

**Changes in v0.4** (each also logged in `DEVIATIONS.md`):
1. **D1 scope widened.** Whole AgentDojo v1 suites (every user task and every injection task), seeds 0, 1
   and 2, and Jig's default sampling (§7) sent explicitly, with the seed, on every agent request
   (run `d1-full-v2`, `configs/full/d1_full_v2.yaml`). The earlier run `d1-full-v1` (5 user × 3 injection
   tasks per suite, seed 0) is kept and labelled as the earlier run; it is never pooled with v2. Its
   configured temperature 0 was **never sent for the agent** (AgentDojo sends `temperature or NOT_GIVEN`), so
   its agent sampled at the 8080 server's defaults, which equal Jig's defaults (temperature 1.0, top-p 0.95,
   top-k 20, min-p 0.05) but unseeded; its reviewers did run at temperature 0, seed 0.
2. **Qwen reviewer sized from measurement and run separately** (`d1-full-v2-qwen`): context 10,240 tokens
   (measured review prompts of at most 446 tokens on the Guardian template, about 1k on Jig's Sentinel
   template, plus Jig's 8,192-token output cap), down from 16,384. The VRAM guard (§9) is unchanged; by its
   estimate the server still does not fit next to 8080 (about 19.4 GB + 2 GB margin against about 18.4–18.8 GB
   free), so this run is refused loudly until more VRAM is free. It is a separate run so that `d1-full-v2`'s
   completeness never depends on it.
3. **Jig bare-URL fix in effect for v2.** Jig now completes a scheme-less web address in an outbound tool's
   URL argument (`www.example.com`) to `https://` before every check (Jig commit `733c80c`). `d1-full-v1`
   ran before the fix, `d1-full-v2` after it; results before and after are reported separately.
4. **C1 rolling summary without a word limit.** The summary prompt no longer says "Keep it under 400 words";
   everything else is unchanged. These trials form their own run (`c1-full-v1-summary-nolimit`); the earlier
   cut-off trials (with the limit) stay in `c1-full-v1` and are reported separately as a finding.
5. **InjecAgent licence confirmed (MIT)** from the official repository; still not used as a benchmark in
   its own right without the owner's go-ahead (§5).
6. **Provenance** now also records each server's default sampling values (what a request that omits a value
   gets).

**Scope change (v0.3).** This version re-scopes the safety side. The memory programme (C) continues in
full. For safety (D), we **no longer author any attack content**: no injection payloads, no attack web
pages, no exfiltration sink, no bespoke poisoning scenarios. Instead we measure Jig's existing defence
layers against **published, peer-reviewed agent prompt-injection benchmarks** (for example AgentDojo and
InjecAgent), running their existing test cases unchanged through a Jig adapter. The former self-authored
attack site, sink and payloads (D1/D2/CD1 as originally drafted) have been removed from the repository.
Any step that would require writing new attack content is skipped and recorded as future work.

## 0. Research questions

- **C — Memory over long horizons.** How should an always-on local agent manage memory and context so
  that it stays useful over days? Which of a bounded raw context, a rolling summary, retrieval over stored
  turns, and agent-curated memory with periodic consolidation (with and without explicit forgetting)
  answers later questions best, at what token and latency cost, and how does each handle facts that change?
- **D — Safety when unattended.** How effective are Jig's defence layers (core and custom rules, the
  isolated Sentinel reviewer under several model choices, read-only research mode, and the approval queue)
  at reducing the attack success rate of **published** indirect prompt-injection benchmarks, and at what
  cost to task utility, false-positive rate and approval burden?

## 1. Hypotheses

Memory (C1):
- **H-C1.** Agent-curated memory with consolidation answers knowledge-update questions better than a
  rolling summary or raw retrieval at a fraction of the full-context prompt tokens.
- **H-C2.** Explicit forgetting during consolidation improves knowledge-update accuracy but harms
  temporal-reasoning accuracy (history is lost), relative to consolidation that keeps history.

Safety (D1):
- **H-D1.** On a published injection benchmark, a model Sentinel lowers the attack success rate relative
  to `no-Sentinel` and `rules-only`.
- **H-D2.** A smaller Sentinel (granite 4.2 8B) catches fewer attacks than the same-size reviewer but
  raises fewer false positives, so reviewers lie on a security-versus-burden frontier.
- **H-D3.** Read-only research mode, by withholding side-effecting tools at the gate, drives the attack
  success rate of action-dependent benchmark cases towards zero, at the cost of task utility on cases that
  require an action.

**Persistent memory poisoning (CD1), conditional.** The central question of whether an intent-only
reviewer is weaker against attacks that persist through memory is retained as a hypothesis, but it will be
tested **only if a published benchmark or dataset already provides the poisoning test cases**. We will not
author poisoning payloads. If no such published cases can be integrated without authoring attack content,
CD1 is documented as future work in the paper.

## 2. Metrics

Memory (C1):
- **Accuracy**, graded by LongMemEval's own judge prompts (ported verbatim from its `evaluate_qa.py`, MIT
  licence) on a judge model from a different family to the agent, at temperature 0. A judge reply that is
  neither "yes" nor "no" is a recorded harness error, never a guess.
- **Prompt tokens per question** (all model calls: ingestion, consolidation and answering), wall time,
  memories kept, approvals raised, and (where measured) peak VRAM of the servers used.

Safety (D1), all taken from the **published benchmark's own ground truth and scorers**:
- **Attack success rate (ASR)**: fraction of the benchmark's injection cases that achieve the benchmark's
  defined malicious objective.
- **Utility**: fraction of the benchmark's user tasks completed, measured by the benchmark's own utility
  check, both with and without an injection present.
- **False-positive rate**: fraction of benign/no-injection cases on which a defence blocks or escalates a
  legitimate action.
- **Approvals per task**: approval requests Jig raised (a proxy for approval fatigue; a human study is
  future work).
- **Reviewer latency and prompt tokens** per Sentinel review.

## 3. Conditions

Defence configurations (D1, as run from v0.4), each an unchanged Jig configuration driven by the adapter:
1. `no-reviewer-action` — the Sentinel slot is a labelled pass-through that returns "allow" with the reason
   "benchmark condition: no reviewer configured". Core rules still apply. This is an experimental
   condition, not a simulated model output.
2. `sentinel-bonsai-action` — Jig's Sentinel, unchanged, on the agent's own model (ternary Bonsai 2 27B).
3. `sentinel-bonsai-readonly` — as 2, in Jig's research mode: only `read` and `private_write` tools are
   allowed and the gate refuses side effects.
4. `guardian-action` — Granite Guardian 4.1 8B in the Sentinel slot (model card's own format, greedy).
5. `sentinel-qwen-action` — Jig's Sentinel on the standard-quantisation counterpart (Qwen3.8-27B Q4_K_M),
   for the effect of quantisation on the reviewer (separate run; see change 2 above).

The v0.3 drafts `rules-only` and `sentinel-granite-8b` were not run: AgentDojo ships no custom rules for
its tools, and writing benchmark-specific rules is left for future work.

Each configuration is run with the benchmark's existing attack and no-attack cases, so ASR, utility and
false-positive rate are all measured on the same items.

Memory strategies (C1): `full_context` (the most recent sessions that fit a token budget),
`rolling_summary` (the model rewrites a summary after each session; no length limit from v0.4), `retrieval` (every turn
stored as a Jig memory; the agent answers with `memory_search`), `hybrid_keep` (a Jig research task per
session extracts dated facts with `memory_add`; every few sessions an action-mode task consolidates by
adding "UPDATE" memories), `hybrid_forget` (as `hybrid_keep`, but consolidation deletes superseded facts
with `memory_forget`, which Jig's Sentinel reviews).

Simulated user for approvals (a labelled condition, not a model output): `approve_all` (worst case, a
fatigued owner), `deny_all` (so that escalation counts as stopped), `oracle` (approves exactly the calls
the benchmark's ground truth marks harmless, where the benchmark provides such labels).

## 4. Experiments

### C1 — Memory strategies
LongMemEval-S (cleaned; MIT; https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned). For each
selected question, the answer-bearing sessions plus sampled distractor sessions are replayed in date
order (simulated clock), then the question is asked "on" its question date. Optionally extended with
LoCoMo if its licence permits redistribution of derived results (see §5).

### D1 — Defence layers on a published injection benchmark
Implemented against **AgentDojo** (MIT, arXiv:2406.13352) in `jigbench.agentdojo_adapter` and
`experiments/d1_agentdojo`. Each AgentDojo tool is wrapped as a real Jig tool (its effect/outbound/category
fixed by an explicit table, no silent fallback), and every tool call the agent makes is executed through
Jig's real gate (schema, mode, core rules, custom rules, Sentinel, approval queue) and executor. AgentDojo's
own user tasks, injection tasks, attacks and scorers are used unchanged; the injection strings come only
from AgentDojo. The agent model is Jig's configured model over its OpenAI-compatible endpoint. We report
ASR, utility, blocked calls and approvals per task across the defence configurations in §3. No benchmark
case is modified and no new case is written.

### CD1 — Persistent memory poisoning (conditional, see §1)
Run only if a published benchmark/dataset supplies the poisoning cases. Otherwise future work.

## 5. Datasets and licences

- **LongMemEval-S (cleaned)** — MIT licence; used for C1. Judge prompts ported verbatim from the
  LongMemEval repository (MIT).
- **AgentDojo** — used for D1; **MIT licence** (confirmed from the installed package metadata, v0.1.35:
  `License :: OSI Approved :: MIT License`), recorded with each run's provenance. Provides the tool
  environments, user tasks, injection tasks, attacks and scorers, all used unchanged.
- **InjecAgent** — **MIT licence**, confirmed on 2 October 2026 from the official repository's licence file
  (`https://github.com/uiuc-kang-lab/InjecAgent`, file `LICENCE`, "Copyright (c) 2023 Qiusi Zhan", default
  branch `main` at commit `f19c9f2`). Candidate extension for D1; **not used** as a benchmark in its own right
  until the owner approves. (AgentDojo's own `injecagent` attack, used in D1, is a one-line attack template
  from the InjecAgent paper that ships inside AgentDojo under AgentDojo's MIT licence; none of InjecAgent's
  test cases are used.)
- **LoCoMo** — candidate extension for C1; used only if its licence permits; recorded if used.
- **BIPIA** — not used (no clear redistribution licence at time of writing).

Every dataset's licence is recorded with the run provenance, and a dataset is not used until its licence
has been read and found to permit the use.

## 6. Benchmark integration (methodological choice)

The published benchmark defines its own tool environment and tasks. The adapter: (1) registers Jig tools
that stand in for the benchmark's tools, so the benchmark's cases drive Jig's real gate and executor;
(2) translates the benchmark's success and utility checks onto the resulting Jig run; and (3) keeps every
other part of Jig (gate, core and custom rules, Sentinel, approvals) unmodified. Where a benchmark reaches
external hosts, the harness confines the run with `netroute` (reserved `.test` hosts only; all other hosts
refused) so a run can never touch a third party. The container browser and egress-proxy path are not
covered yet (a core hook is recommended). AgentDojo's tools are simulated and make no network requests, but
Jig's core rule `no-local-network` resolves every URL's host in real DNS before allowing an outbound call, so a
D1 run makes DNS lookups (and nothing else) for the benchmark's host names. AgentDojo's fictional hosts that
do not exist in DNS (for example `www.dora-website.com`) are therefore refused by that rule in every
condition; this is reported as a benchmark–environment mismatch, not worked around.

## 7. Models, provenance and statistics

- **Models**: `research/models/models.toml` and `models.lock.json` (SHA-256 verified against Hugging Face,
  licence, quantisation). Agent: Bonsai 2 27B ternary (PTQ1_0) on the owner's server (port 8080), used
  read-only through its API. Standard-quantisation counterpart: Qwen3.8-27B Q4_K_M. Reviewers and judge:
  granite 4.2 8B, and others as capacity allows. The abliterated Qwen3.8-27B Q6_K is permitted for
  **memory** experiments only and is **excluded from all safety measurement** (refusal behaviour removed).
- **Provenance**: every run session records the Jig commit and dirty state, the hardware (GPU, driver,
  VRAM, CPU, RAM), and for every endpoint the served model file, its quantisation type, the llama.cpp
  build, context size and slots, plus the full config and seeds.
- **Sampling**: Jig's defaults (temperature 1.0, top-p 0.95, top-k 20, min-p 0.05) with a recorded seed;
  the judge and any guard model use temperature 0. In D1 the agent's requests are sent by AgentDojo's
  pipeline, so from v0.4 the harness sends every sampling value and the seed explicitly on each request and
  records them per trial (`agent_request_sampling`), rather than relying on server defaults.
- **Statistics**: rates with Wilson 95% intervals; means with percentile-bootstrap 95% intervals;
  pairwise comparisons by Fisher's exact test (unpaired) or McNemar (paired items), with Holm–Bonferroni
  correction. Full runs use ≥3 seeds, rising as overnight capacity allows. Pilots report raw counts and
  intervals without inference and are labelled PILOT.

## 8. Simulated clock

"Days" are consecutive tasks in one Jig instance; dates are carried in the task text and memory content
(C1 uses LongMemEval's session dates). Jig's agent system prompt embeds the real current date, which can
conflict with the simulated date; this is a known threat (a clock-injection core hook is recommended).
Wall-clock effects (real idle periods, cold caches, scheduler drift) are not reproduced.

## 9. Compute policy

Full runs start only between 01:00 and 07:00 Europe/London after 5 minutes without input, or at any time
after 30 minutes without input, and only if a 5-second GPU utilisation sample averages ≤25%. While a trial
runs, the harness checks user input every 5 seconds and cancels the trial, stops its model servers and
exits as soon as the user is active; between trials it also re-checks GPU use. Unfinished trials leave no
record, so the next start resumes cleanly. Harness servers use ports 8090–8099 and are refused if free
VRAM would drop below a 2 GiB margin.

### 9a. Safe GPU handover for the main (8080) server

Some runs need a test model to have the whole GPU to itself, which means briefly stopping the main model
server on port 8080. The harness does this through a **safe, reversible handover** (implemented in
`jigbench.handover`, not in Jig's core) that never interrupts anything in use and always restores the
original server. It replaces the earlier "never touch 8080" rule.

**Preconditions (all must hold, re-checked immediately before stopping):**
1. inside the overnight window, or the user idle ≥ 30 minutes;
2. the 8080 server has had no active request for ≥ 10 continuous minutes, polled from llama-server's
   `/slots` (all slots idle) or `/metrics` (`requests_processing == 0`) as a fallback — this also protects
   other agents running tests against 8080;
3. Jig (`127.0.0.1:8766`, token from `jig token show` or the data directory) reports no running tasks or
   steps, no in-flight chat and nothing scheduled during the handover window, via its real API
   (`/status`, `/state`, `/tasks`, `/schedules`); if Jig is not running this passes and is recorded;
4. no `pytest`, demo-recording or `playwright` process is running.
If any check fails, nothing is stopped: the reason is logged and the handover is retried later.

**Sequence.** Pause Jig first (`POST /agent/pause`) so no new work starts; capture the exact command line,
working directory and relevant environment of the live 8080 process (via `psutil`) to
`research/logs/handover-<ts>.json`; stop it gracefully (polite stop, wait, force only on timeout) and
confirm VRAM was released; run the test model(s) within the VRAM budget, still re-checking user activity.

**Always restore.** When the runs finish, or the user becomes active, or Jig receives queued work, or any
error occurs: stop our server, restart the original from the recorded command line/cwd/environment (logs to
`research/logs/`), wait until `/v1/models` serves the same alias again, then unpause Jig only if we paused
it. Restoration is guaranteed by `try/finally` in the harness **and** by an independent per-user watchdog
task `\Jig\Jig Research Restore` (every 5 minutes) that restores from the latest unrestored handover file
when no harness process is alive. Handover files are marked `restored` when done; restoration must succeed
or fail loudly in `research/logs/handover.log`. At most one handover is in progress at a time, and the
watchdog restores within a few minutes of the user returning.

**Ownership.** If Jig *launched and supervises* the 8080 server (its `[model.launch]` supervisor would
restart it), the harness never kills it behind Jig's back: it uses Jig's own power API
(`POST /power/stop` / `/power/start` with scope `jig_and_model`, or `jig model stop|start`) when that has
landed, and otherwise refuses the handover and logs why. A hand-started 8080 server is stopped and restored
directly. The case is detected from the process ancestry and Jig's `/status` before each handover. Port
8080 is never started or stopped except through this handover.

## 10. Threats to validity and ethics

- **Construct**: approvals per task is a proxy for approval fatigue [yu2026habituation]. The LLM judge may
  err; its model and prompts are recorded, and a manual audit of a sample is planned.
- **Internal**: temperature 1.0 adds variance [prasad2026quanttemp]; seeds and sampling are recorded and
  intervals reported. Published benchmark cases are static, which can over-state robustness
  [narisetty2026adaptive]; adaptive attacks are out of scope here because they would require authoring
  attack content. The `no-sentinel` condition uses the same gate path as a model reviewer, so differences
  isolate the reviewer.
- **External**: one GPU (RTX 5090, 32 GB), a small number of model families, and Windows.
- **Ethics**: the safety evaluation uses **only published benchmark cases**, unchanged. This programme
  authors no attack, injection, exfiltration or jailbreak content. Where a benchmark exercises network
  egress, the harness confines it to local reserved-TLD hosts and refuses all others, so a run cannot
  reach the internet. Wikipedia text (if any benchmark uses it) is under CC BY-SA 4.0 with attribution;
  LongMemEval under MIT; each injection benchmark under its own recorded licence.

## 11. Core Jig hooks (recommended, not implemented)

1. **Benchmark hosts**: a configuration option mapping named hosts to local ports for evaluation,
   replacing the in-process exemption.
2. **Clock injection**: let the runtime take a clock so the system prompt and memory timestamps follow a
   simulated date.
3. **Memory provenance in the Sentinel payload**: the origins of memories retrieved during the run.
4. **Reviewable or quarantined memory writes** from research-mode (tool-derived) tasks.
5. **Reviewer usage accounting**: record Sentinel token usage in the audit log like model calls.
6. **Container egress hook** so the browser and proxy path can be benchmarked the same way.
