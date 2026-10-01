# Literature review: memory and unattended safety for always-on local agents

Version 0.1, 1 October 2026. Living document; regenerate the bibliography with
`verify_citations.py` whenever entries are added.

## How this review was done, and its limits

- Every paper cited here was **fetched from the arXiv API** by `verify_citations.py`, which records the
  title, authors and year that arXiv returns and refuses to cite an entry whose fetched title does not
  match. The result is in `verified.json` and `references.bib` (61 arXiv entries and 1 web page, all
  verified on 2026-10-01). Abstracts were fetched with `fetch_abstracts.py` and read before writing each
  summary below. Statements about a paper's results come from its abstract unless marked otherwise; full
  texts of the closest works should be read before the paper's first public version (tracked as a TODO).
- Candidate papers were found with arXiv's own search API (`search_arxiv.py`). A general web search was
  **not available** in this session, so work that exists only outside arXiv (industry reports,
  conference-only papers, blog posts) is under-represented. This is a real gap in coverage, not a claim
  that such work does not exist.
- The motivating product events (Meta's Muse on 8 September 2026 and OpenAI's Dots on 29 September 2026,
  and the Reuters report on Muse's internal tests) come from the project brief. **I could not fetch a
  primary source for them in this session**, so the paper describes them only as motivation and does not
  cite them until a source URL has been fetched and checked.
- Benchmark licences were read from the GitHub and Hugging Face APIs (`fetch_web_sources.py`,
  `web_sources.json`): AgentDojo MIT, LongMemEval MIT (code and the cleaned dataset on Hugging Face),
  InjecAgent MIT; LoCoMo and BIPIA report `NOASSERTION` through the API, so their licence must be read
  from the repository before any data is redistributed.

## 1. Long-term memory and context management

**Architectures.** MemGPT [packer2023memgpt] treats the context window as fast memory and pages
information to and from slower stores ("virtual context management"). Generative Agents
[park2023generative] keep a full natural-language record of experience, retrieve it by recency,
importance and relevance, and periodically *reflect* into higher-level memories, which is the template
for consolidation. MemoryBank [zhong2023memorybank] adds an Ebbinghaus-inspired forgetting curve. A-MEM
[xu2025amem] organises memories as Zettelkasten-style linked notes that the agent itself updates. Mem0
[chhikara2025mem0] extracts, consolidates and retrieves salient facts (with a graph variant) and reports
lower latency and token cost than full-context baselines. Zep [rasmussen2025zep] uses a temporal
knowledge graph, which is directly relevant to staleness because facts carry validity intervals. CoALA
[sumers2023coala] gives the vocabulary (working, episodic, semantic, procedural memory) used in our
protocol. Lost in the Middle [liu2023lost] shows that long contexts are used unevenly, which is the basic
argument against simply appending everything.

**Benchmarks.** LoCoMo [maharana2024locomo] evaluates very long conversations (many sessions) for QA,
event summarisation and multimodal dialogue. LongMemEval [wu2024longmemeval] tests five abilities:
information extraction, multi-session reasoning, temporal reasoning, *knowledge updates* and
*abstention*; the last two are exactly our staleness and contradiction questions. Its code and the
cleaned dataset are MIT-licensed, so we can adapt it. In 2026 the field moved towards agentic and
lifecycle settings: MemoryArena [he2026memoryarena] finds that agents near saturation on LoCoMo do poorly
when memory must guide later *actions*; Memora [uddin2026memora] spans weeks to months and introduces a
forgetting-aware accuracy (FAMA) that penalises use of invalidated memories; LongMemEval-V2
[wu2026longmemevalv2] evaluates environment experience for web agents. Annapureddy and Thamatani
[annapureddy2026epistemics] argue that the consolidation decision itself should be measured and
governed.

**Compaction as a safety surface.** Governance Decay [chen2026governancedecay] shows that constraints
an agent obeys while they are visible are silently removed by compaction (violations rise from 0% to 30%
on average after compaction), and that adversarial content can bias the summariser to drop a policy.
COMPINT [wang2026lostincompaction] finds that compactors retain only 17% of user session constraints on
average. Manufactured Confidence [kwon2026manufactured] shows that memory rewriting turns hedged remarks
into confident "facts" that agents then obey, and AuthMem-Bench [zhan2026authmem] finds *authority
collapse* (a stored claim losing the source constraints on its use) in 48 of 49 configurations, with
persisted authority labels cutting unauthorised actions from 16.9% to 0.0% end to end.

**What this means for (C).** Compaction, summarisation, retrieval and forgetting are well studied
individually, and lifecycle benchmarks now exist. What is less covered is the *operating point that a
local user actually has*: a fixed, modest context budget (32–64K tokens) on a quantised model running on
one consumer GPU, with an always-on scheduler, where tokens and latency are the scarce resources. We did
not find a study comparing raw append, rolling summary, retrieval-only and hybrid consolidation (with
and without explicit forgetting) under that budget, on ternary versus standard 4-bit quantisation of the
same base model.

## 2. Indirect prompt injection and agent safety

Greshake et al. [greshake2023indirect] defined indirect prompt injection: instructions planted in data
the application retrieves. Liu et al. [liu2023formalizing] formalised attacks and defences; BIPIA
[yi2023bipia] benchmarked them. For tool-using agents, InjecAgent [zhan2024injecagent] measures direct
harm and data-stealing attacks through tool outputs; AgentDojo [debenedetti2024agentdojo] is an
extensible environment with 97 realistic tasks and adaptive attacks (MIT, integrable); Agent Security
Bench [zhang2024asb] covers many scenarios including memory poisoning; WASP [evtimov2025wasp] tests
end-to-end web-agent hijacking; AgentHarm [andriushchenko2024agentharm] measures harmfulness when the
*user* is malicious (out of our threat model, but a useful negative control); ToolEmu [ruan2023toolemu]
uses an LM-emulated sandbox and an LM safety evaluator. We deliberately do not emulate tools: our
environments are real local services.

Exfiltration through egress is directly relevant to Jig's gated egress proxy. Silent Egress
[lan2026silentegress] uses a fully local testbed with a qwen2.5:7b agent and reports that a malicious
page induces exfiltrating requests with probability 0.89, and that 95% of successful attacks are missed
by output-based checks; sharded exfiltration evades simple data-loss prevention. Narisetty et al.
[narisetty2026adaptive] warn that out-of-band defences (CaMeL-like capability systems) have so far been
validated only on static attack sets, and that adaptive attacks broke twelve in-band defences at over
90% success. This shapes our protocol: we report static-attack results as an *upper bound on
robustness* and include an adaptive-attacker condition as future work.

## 3. Memory and RAG poisoning (where C and D meet)

Early work assumed write access to the store: PoisonedRAG [zou2024poisonedrag] and AgentPoison
[chen2024agentpoison] poison knowledge bases or memories with optimised triggers. MINJA [dong2025minja]
injects through queries only, and MemoryGraft [srivastava2025memorygraft] plants malicious "successful
experiences". 2026 brought a wave of persistence-focused work that **already covers the core of our
proposed CD1 experiment**:

- eTAMP [zou2026etamp]: a single contaminated observation poisons memory and activates on *other* sites
  in later sessions (up to 32.5% attack success on GPT-5-mini); more capable models are not more secure.
- Sleeper memory poisoning [pulipaka2026sleeper]: delayed attacks via documents or web pages; poisoned
  memories are written in up to 99.8% of trials and drive attacker-intended actions in 60–89% of
  successful retrievals.
- Cross-session stored prompt injection [xie2026crosssession] frames persistence as "stored XSS for
  agents" and argues for state-centric security.
- MPBench [dash2026mpbench]: four write channels, six attack classes; existing prompt-injection defences
  fail to cover memory poisoning.
- WhisperBench / MemGhost [zhang2026whisperbench]: stealthy one-email memory injection in persistent
  personal agents (OpenClaw, Claude Code SDK), robust to input-, model- and system-level defences.
- Bad Memory [gadgil2026badmemory]: hard to make agents overwrite their own memory files from external
  content, but payloads already in memory attack current and future sessions.
- MemSecBench [chen2026memsecbench]: a Write–Execute–Forget lifecycle over 24 configurations; malicious
  memory persists in 84.2% of cases, the full chain succeeds in 50.3%, and selective repair works in
  56.1%.
- PMPA [huang2026pmpa]: persistent memory poisoning on OpenClaw and Claude Code; a prompt-level defence
  reduces injection but gives little protection once memory is poisoned.
- A lifecycle survey [lin2026ltmsurvey] argues that memory security must be anchored in storage-time
  provenance, versioning and retention policy; MemGate [zhang2026memgate] gates retrieval by task
  relevance rather than similarity.

**Honest assessment.** "An injected memory written on day N triggers on day N+k" is no longer novel by
itself, and provenance labels have been shown to work [zhan2026authmem]. The CD1 contribution must
therefore be narrower and comparative: (i) an *isolated action reviewer* as the defence, (ii) the
reviewer's model size and family, (iii) the interaction with the memory strategy from (C), and (iv)
small and extremely quantised local models.

## 4. Guard and reviewer models, and system-level defences

Classifier guards such as Llama Guard [inan2023llamaguard], ShieldGemma [zeng2024shieldgemma] and Granite
Guardian [padhi2024graniteguardian] classify prompts and responses against a harm taxonomy; Granite
Guardian also covers jailbreaks and RAG groundedness. LlamaFirewall [chennabasappa2025llamafirewall]
combines a jailbreak detector, chain-of-thought alignment auditing and static code analysis as a final
layer for agents. GuardAgent [xiang2024guardagent] turns safety requests into guardrail code. On the
design side, Willison's dual-LLM pattern [willison2023dualllm] separates a privileged model from a
quarantined one; CaMeL [debenedetti2025camel] extracts control and data flow from the trusted query so
untrusted data cannot change the program, plus capabilities against exfiltration; Beurer-Kellner et al.
[beurerkellner2025patterns] catalogue provable design patterns and their utility costs; spotlighting
[hines2024spotlighting] and the instruction hierarchy [wallace2024hierarchy] are in-model defences. PACT
[fan2026pact] argues that trust must be tracked per *argument*, not per tool call, because
invocation-level monitors give false positives or false negatives in mixed-trust workflows.

Jig's Sentinel is closest to an **action-level, intent-conditioned monitor**: a separate model call that
sees the trusted user intent, the proposed tool call and the policy findings, but not the conversation.
It is not CaMeL (no data-flow tracking) and not a harm classifier (it judges fit to intent). The closest
evaluation of monitor strength is Kale et al. [kale2025weaktostrong], who show that monitor scaffolding
matters more than monitor awareness and that weaker models can monitor stronger agents with the right
scaffolding, on SHADE-Arena with hosted models. We found no evaluation of an intent-only action reviewer
against *memory-mediated* attacks, where the malicious goal reaches the agent from its own memory and the
proposed action can look consistent with a plausible intent.

**Approval burden.** Habituation at the Gate [yu2026habituation] measures rising approval and falling
scrutiny in human review of AI-agent pull requests over seven months, which supports using approvals per
task as a proxy for fatigue risk. Loopjacking [kumar2026loopjacking] shows that the approved operation
must be bound exactly to the executed one; Jig binds approvals to a specific tool call id and arguments,
which our harness checks.

**Quantisation and safety.** Kharinaev et al. [kharinaev2025quant] find that post-training and
quantisation-aware methods can degrade safety alignment, with no method consistently best; Prasad and Pal
[prasad2026quanttemp] find standard INT4 quantisation roughly safety-neutral for 7 of 8 models but large
instability at higher sampling temperatures. Both use harmfulness benchmarks on chat models, not
agentic injection, and neither covers ternary (about 1.75 bits per weight) models. Jig's default
sampling temperature is 1.0, which [prasad2026quanttemp] flags as a risk factor; we record sampling
settings with every run.

## 5. Always-on and proactive agents

Proactive Agent [lu2024proactive] trains agents to anticipate tasks from observed human activity; the
2026 Foundations of Proactive Agents [oh2026proactivefoundations] proposes principles (task capability,
temporal allocation, trust) and a multi-day simulation testbed, Proactivity-Gym. ReAct
[yao2022react] is the basic reason-and-act loop underlying Jig's agent. Proactive research mode in Jig
(read-only tools plus private notes and memory writes) means that proactive work is exactly the channel
through which untrusted content enters memory without a human in the loop, which ties the always-on
setting to CD1.

## 6. The precise gap this paper fills

Prior work already establishes that (a) indirect injection hijacks tool-using agents, (b) poisoned
memories persist and trigger later, (c) compaction and consolidation drop constraints and inflate
authority, (d) provenance and authority labels help, and (e) weaker monitors can supervise stronger
agents with good scaffolding. We do **not** claim any of these as new.

The gap we target is the intersection that a person running an always-on agent on their own hardware
faces, measured in one open, re-runnable harness:

1. **Isolated action review against memory-mediated attacks.** How often does an intent-only Sentinel
   (no conversation access) catch actions whose malicious goal came from a memory written days earlier
   during unattended read-only research, compared with direct injection in the same session? And which
   cheap defences around the memory store (provenance tags shown to the reviewer, review of memory
   writes, quarantine of tool-derived memories, user-visible memory) close the gap?
2. **Reviewer choice under a local budget.** Catch rate, false-positive rate and *approval requests per
   task* for no reviewer, rules only, the same model, a smaller model, a different family and a dedicated
   guard model, all on one consumer GPU, so the security–burden trade-off can be read directly.
3. **Memory strategy as a security variable.** Whether rolling summaries and periodic consolidation
   preserve, launder or remove poisoned content compared with retrieval-only and raw append, alongside
   the usual accuracy, staleness, token and latency measures.
4. **Extreme quantisation.** The same base model (Qwen3.8-27B) as a ternary PTQ (Bonsai 2) and as
   standard Q4_K_M, as agent and as reviewer. We found no agentic injection or memory study at this
   precision.
5. **A living benchmark for local models.** Results are kept with full provenance (model file hash,
   quantisation, server build, hardware, seeds, commit) and can be re-run by others on new models.

Items 1 and 2 are the most likely to be genuinely new; items 3 and 4 extend existing findings to a new
setting; item 5 is an engineering contribution.

## References

See `references.bib` (generated). Keys used above match the BibTeX keys; each entry records the arXiv
id, the fetched title and authors, and the verification date.
