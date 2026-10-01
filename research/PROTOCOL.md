# Research protocol: optimising always-on local agents (memory and unattended safety)

Version 0.1, 1 October 2026. Living document. Author: Robyn Le Sueur. The testbed is Jig
(Apache-2.0). All experiments run against the **real** Jig runtime and **real** local models; no model
output is faked and no service is stubbed. Constructed scenarios are labelled as benchmark data.

## 0. Research questions

- **C — Memory over long horizons.** How should an always-on local agent manage memory and context so
  that it stays useful over days without drift or bloat? Which of raw append, rolling summary,
  retrieval-only, and hybrid (retrieval + periodic consolidation), with and without explicit forgetting,
  gives the best accuracy on later questions at an acceptable token and latency cost, and how do they
  handle staleness (facts that change) and contradiction (facts that conflict)?
- **D — Safety when unattended.** How effective is Jig's isolated Sentinel reviewer, as a function of
  the reviewer model (none, rules only, same model, smaller model, different family, dedicated guard)?
  How robust is the agent to indirect prompt injection from web pages and documents, including injection
  that **persists through memory** and triggers days later? What is the data-exfiltration catch rate, and
  what is the approval burden (approvals per task) that each configuration imposes?

## 1. Hypotheses

- **H-C1.** Hybrid retrieval with periodic consolidation and explicit forgetting gives the best later-
  answer accuracy per 1,000 context tokens, and the lowest FAMA-style penalty for using stale memories,
  compared with raw append (most tokens), rolling summary (worst staleness), and retrieval-only.
- **H-C2.** Rolling-summary and consolidation strategies *launder provenance*: a tool-derived, untrusted
  memory loses its source tag after consolidation more often than under raw append or retrieval-only.
  (This links directly to D.)
- **H-D1.** A Sentinel improves the catch rate of harmful outbound/side-effecting actions over "none"
  and "rules only"; the same-model Sentinel is strongest, a different-family small model is close, and a
  dedicated guard model has the highest catch rate but also the highest false-positive rate.
- **H-D2.** Catch rate falls as the reviewer shrinks, but **approvals per task** also fall, so there is a
  measurable security-versus-burden frontier; some small models sit on it and some are dominated.
- **H-D3 (the central one).** For **memory-mediated** attacks (poison on day N, trigger on day N+k), an
  intent-only Sentinel catches *fewer* attacks than for same-session direct injection, because the
  proposed action can look consistent with a plausible intent once the malicious goal is laundered into a
  trusted-looking memory. Showing the reviewer the memory's provenance, and/or quarantining tool-derived
  memories, recovers most of the lost catch rate.
- **H-D4.** Ternary (PTQ1_0, ~1.75 bpw) and standard Q4_K_M of the *same* base model differ in catch
  rate and false-positive rate by a margin that is small relative to the difference between model
  families, i.e. quantisation matters less than family for reviewing.

## 2. Metrics

Memory / context (C):
- **Later-answer accuracy**: correctness on questions asked in sessions after the facts were introduced,
  graded by a deterministic checker where possible and otherwise by an LLM judge run on a *separate*
  local model from the agent, with the judge prompt and model recorded. Report exact-match and judged
  accuracy separately.
- **Staleness handling**: accuracy on questions whose answer changed, using a forgetting-aware score
  (adapted from Memora's FAMA): credit for the current value, penalty for asserting a superseded value.
- **Contradiction handling**: when two memories conflict, does the agent flag the conflict, prefer the
  newer/‑higher-provenance one, or silently pick one? Scored on a labelled rubric.
- **Context tokens used** per answered question (from server `usage`), and **wall-clock latency** per
  task. **Memory store size** over time (rows, bytes).

Safety (D):
- **Catch rate (recall)**: fraction of truly harmful actions that are denied or escalated to approval
  before execution.
- **False-positive rate**: fraction of benign actions that are denied or needlessly escalated.
- **Approvals per task**: mean number of approval requests raised per task (proxy for approval fatigue;
  a human study is explicitly future work).
- **Attack success rate (ASR)**: fraction of attack scenarios where the harmful effect occurs (e.g. a
  blocked-host egress, a disallowed file write, a secret leaving the machine against a test sink).
- **Injection-to-memory rate** and **cross-session trigger rate** for CD1 (write succeeded; later
  retrieval drove the harmful action), following the Write–Execute–Forget framing of MemSecBench.
- **Sentinel latency** per review and **extra tokens** per task.

## 3. Experimental conditions

Reviewer configurations (D1, applied throughout D):
1. `none` — Sentinel disabled (core rules still apply; this isolates the reviewer's marginal value).
2. `rules-only` — core + custom rules, no model review.
3. `same` — Sentinel = agent model (Bonsai 27B ternary, or Qwen3.8-27B Q4_K_M).
4. `smaller` — small same-ish lineage (Qwen3-VL-8B) and small other family (Granite 4.2 8B, Gemma 4 E4B).
5. `different-family` — e.g. agent Bonsai, Sentinel Gemma/Granite.
6. `guard` — Granite Guardian 4.1 8B as the reviewer.

Agent models: Bonsai 2 27B (ternary, port 8080, read-only via API), Qwen3.8-27B Q4_K_M, Gemma 4 26B-A4B.
Memory strategies (C1): `raw-append`, `rolling-summary`, `retrieval-only`, `hybrid-consolidate`, each
`±forget`. These are implemented in the harness **on top of** Jig's public memory API
(`POST/GET/PATCH/DELETE /memory`, `memory_search`), not by editing `jig/`.

## 4. Experiments

### C1 — Memory strategies over long multi-session streams
Event streams are built from **real public data** (e.g. a user's evolving preferences expressed over
"days", seeded from public FAQ/wiki text and from LongMemEval-style multi-session histories, licence
permitting). Long horizons are **compressed in time** by replaying the stream through Jig's scheduler
with a simulated clock (see §6). For each strategy we run the same stream, then ask a fixed later-question
set, and record all metrics in §2. Repeats: ≥5 seeds per cell; report mean and 95% CI.

### D1 — Sentinel effectiveness
A labelled pool of benign and harmful action scenarios (file writes, outbound fetches, egress to blocked
hosts, secret use) is run through the real gate under each reviewer configuration. Harmful items have a
ground-truth "should block/escalate" label; benign items "should allow". Compute catch rate, FPR,
approvals/task, and Sentinel latency. Repeats ≥5 seeds (sampling temperature 1.0 is Jig's default, so
runs vary; we also run a temperature sweep as a secondary analysis, motivated by [prasad2026quanttemp]).

### D2 — Indirect injection via browsed pages and fetched documents
A **real local web server** (static site in Docker, and a plain file server) hosts benign and
injection-laced pages and documents. The real Jig agent fetches them through its real tools
(`web_fetch`, and the container `browser_*` tools where the container backend is used) and its real
egress path. Injection payloads attempt (a) a disallowed action, (b) exfiltration to a **local test
sink** we control, never a third party. ASR, catch rate, FPR and approvals/task per reviewer config.

### CD1 — Persistent memory poisoning (central experiment)
Day N: in read-only research mode, the agent browses a poisoned local page/document that tries to get a
false "fact" or instruction written to memory. Day N+k (simulated): a benign-looking task causes
retrieval of that memory, and we measure whether the harmful action is attempted and whether it is
caught. Defences compared (each implemented via Jig's public API or the harness, not core edits):
- **provenance tags** surfaced to the Sentinel (requires passing memory source into the intent/policy
  payload — a possible core hook, see §8);
- **Sentinel review of memory writes** (treat `memory_add` as reviewable);
- **quarantine of tool-derived memories** (tag-and-withhold from retrieval until confirmed);
- **user-visible memory** (baseline — Jig already exposes memory; measures how much visibility alone
  helps if a review step inspects it).

## 5. Baselines, models, and statistics

- **Baselines**: `none` and `rules-only` for D; `raw-append` and `retrieval-only` for C.
- **Models**: see `research/models/models.toml` and the generated `models.lock.json` (file hash,
  quantisation, licence). All GGUF on llama.cpp/PrismML; sampling settings recorded per run.
- **Statistics**: ≥5 seeds per cell (more for cheap cells); report mean and bootstrap 95% CI
  (10,000 resamples). Compare configurations with paired tests across shared scenarios (Wilcoxon
  signed-rank for paired, Mann–Whitney otherwise); correct for multiple comparisons
  (Holm–Bonferroni). Pilots report raw numbers without inference and are labelled as pilots.

## 6. Simulated clock (methodological choice, documented)

Days are compressed by advancing a logical clock that the harness controls and replaying real event
streams through Jig's scheduler. This tests retrieval, consolidation and trigger-after-delay logic
without waiting real days. It does **not** reproduce wall-clock effects (real background load,
genuinely cold caches). We state this in the paper and, where feasible, confirm one scenario at real
time.

## 7. Threats to validity and ethics

- **Construct validity**: approvals/task is a *proxy* for approval fatigue; the human study is future
  work [yu2026habituation]. LLM-judged accuracy is validated against deterministic checks on a subset.
- **Internal validity**: temperature 1.0 adds variance [prasad2026quanttemp]; we fix and record seeds and
  sampling, and report CIs. Static attack sets over-estimate robustness [narisetty2026adaptive]; we label
  results as static and list adaptive attacks as future work.
- **External validity**: results are specific to the chosen models, quantisations and one GPU; the living
  benchmark exists precisely so others can test more.
- **Ethics and safety of the experiments**: all attacks target **local test services we run**
  (localhost/Docker) and a **local exfiltration sink**; no real third party is ever targeted. Payloads
  live under `research/harness/scenarios/` clearly labelled as benchmark attack data. We do not publish
  novel potent attacks; payloads adapt already-published techniques for defence evaluation. No personal
  data beyond synthetic or public benchmark data is used. Jig's core rules already forbid outbound access
  to localhost and the LAN, so the exfiltration sink is reached only through the deliberately configured
  test path, which we document.

## 8. Possible core Jig hooks (described, not implemented here)

The harness avoids editing `jig/`. Two capabilities would make CD1 cleaner and are recommended for the
core team to consider (see the report):
1. **Memory provenance in the Sentinel payload.** Today the Sentinel sees intent, action and policy, but
   not the provenance of memories that shaped the action. A read-only field (e.g. the sources of
   retrieved memories used this run) would let the reviewer weigh untrusted-origin facts.
2. **Reviewable or quarantined memory writes.** An optional mode where `memory_add` from tool-derived
   content is quarantined (stored with a `quarantined` tag and withheld from `memory_search`) until a
   later confirmation. Both can be prototyped in the harness via tags + a wrapper, which is what the
   pilots do; native support would remove the wrapper.
