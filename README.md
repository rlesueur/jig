# Jig

Jig is an open-source, always-on personal AI agent that runs **only on local models**. It works with any sufficiently capable model behind an OpenAI-compatible endpoint that supports tool calling, such as llama.cpp (including forks), Ollama, LM Studio or vLLM. Your memory, audit trail, rules and secrets stay on your machine, in files you can inspect and edit.

Jig is an open alternative to hosted agents such as Meta's Muse and OpenAI's Dots. It takes their best safety ideas (an isolated reviewer, a credential vault, read-only background research, per-action rules and approvals) and adds the things that running locally makes possible: memory you can see and edit, and an audit trail you own.

> Status: foundations. The runtime, safety model, API and tests are in place. A VM/container sandbox, a browser, voice and messaging channels are on the roadmap.

## Model requirements

Jig is model-agnostic. Nothing in the code assumes a particular model, prompt format or sampling scheme: the endpoint, model name and sampling parameters all come from config. Jig needs:

- **Native tool calling** through the OpenAI `tools` / `tool_calls` API, including well-formed JSON arguments. Parallel tool calls are used when the model offers them.
- **JSON-schema structured output** (`response_format: {type: "json_schema"}`) for the planner and the Sentinel.
- **A context of 32K tokens or more** (advisable). Jig logs a warning at start-up if the server reports less.
- **Decent instruction following**, so it can plan, use tools sensibly and give honest answers when a tool is refused.

Reasoning text (`reasoning_content` or `reasoning`) is optional. When a server sends it, Jig records it in the run steps. When it doesn't, nothing breaks.

At start-up Jig runs a **real capability check** against the configured model. It requests a specific tool call and checks the name and arguments, and it requests a JSON-schema answer and checks the value. If either check fails, Jig refuses to start and explains why. Run the same checks any time with `jig health`.

Jig was developed and tested with **Ternary Bonsai 2 27B** on the PrismML llama.cpp fork (`profiles/llamacpp-bonsai.toml`). That is one example setup, not a requirement.

## Architecture

```
            HTTP / WebSocket API (FastAPI)          CLI: jig serve | chat | health
   chat · goals · tasks · schedules · approvals · rules · memory · audit · vault · /events
                                   │
 ┌─────────────────────────────────┴───────────────────────────────────────┐
 │ Runtime                                                                  │
 │  Heartbeat scheduler ──► task runner ──► Agent loop ──► model client ────┼──► agent model
 │   (schedules, deps,        (resume from    (multi-step     (OpenAI-compat, │    (any OpenAI-
 │    N concurrent tasks)      checkpoints)    tool calls)     streaming)     │     compatible)
 │  Planner (goal → tasks)                        │                          │
 │                                                ▼                          │
 │                           ┌──────────── Gate (every tool call) ──────────┐│
 │                           │ 1 schema  2 mode  3 core rules  4 custom rule││
 │                           │ 5 Sentinel ─────────────────────────────────┼┼──► Sentinel model
 │                           │ 6 approval queue (pause / resume)            ││    (separate call,
 │                           │ 7 vault refs resolved, tool runs, redaction  ││     no tools)
 │                           └──────────────────────────────────────────────┘│
 │  Tools: web_fetch · read_file · list_files · write_file · notes · memory · time
 │  Sandbox (per-agent folder)     Vault (DPAPI / keyring)                   │
 │  SQLite: goals, tasks, runs, steps, approvals, rules, memory+FTS5, notes, audit (append-only)
 │  Event bus ──► avatar state tracker ──► /events (WebSocket) and /events/sse
 └──────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility |
| --- | --- |
| `jig/config.py` | TOML config and environment overrides; separate `[model]` and `[sentinel]` endpoints |
| `jig/model.py` | Async OpenAI-compatible client: streaming, tool calls, optional reasoning, timeouts, health, capability probes |
| `jig/agent/` | Tool-calling loop with a step limit and checkpointed run/step records; prompts; goal planner |
| `jig/tools/` | Decorator-based registry (JSON schema from type hints), built-in tools, network guard |
| `jig/policy/` | Core rules, custom rules, Sentinel, approval queue and the gate |
| `jig/scheduler.py` | Always-on heartbeat: due schedules, dependency-aware task pick-up, concurrency limit |
| `jig/runtime.py` | Wires everything together; start-up checks; crash and restart recovery; chat streaming |
| `jig/memory.py` | Memory store with a pluggable search backend (FTS5 now, embeddings later) |
| `jig/audit.py` | Append-only audit log (SQLite triggers reject updates and deletes) |
| `jig/vault.py` | Credential vault (Windows DPAPI, or `keyring` elsewhere) |
| `jig/sandbox.py` | Per-agent workspace with path-traversal protection |
| `jig/events.py` | Event bus and the derived avatar state |
| `jig/constants.py` | All shared names: avatar states, task variants, modes and statuses |
| `jig/api/app.py` | HTTP and WebSocket API |

## Running it

Requirements: Python 3.11 or newer, and a local OpenAI-compatible server with a tool-capable model.

```powershell
cd jig
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"

# Point jig.toml (or a profile) at your server, then:
.\.venv\Scripts\jig health                    # checks the endpoints and runs the capability probes
.\.venv\Scripts\jig serve                     # always-on agent and API on http://127.0.0.1:8766
.\.venv\Scripts\jig chat --url http://127.0.0.1:8766
```

### Choosing a model server

`jig.toml` points at `http://127.0.0.1:8080/v1` and discovers the model from `/v1/models`. Discovery fails loudly if the server offers more than one model; in that case, set `name`. The example profiles are in `profiles/`:

| Profile | Server |
| --- | --- |
| `profiles/llamacpp-bonsai.toml` | llama.cpp / forks (`llama-server --jinja --parallel 4`); the tested setup |
| `profiles/ollama.toml` | Ollama (`http://127.0.0.1:11434/v1`); set a tool-capable model and a context of 32K or more |
| `profiles/lmstudio.toml` | LM Studio local server (`http://127.0.0.1:1234/v1`) |
| `profiles/vllm.toml` | vLLM with `--enable-auto-tool-choice --tool-call-parser ...` |

Use one with `jig --config profiles/ollama.toml serve`, or set `JIG_CONFIG`. `[model.sampling]` is sent exactly as written, so include only parameters your server accepts. If your server needs an API key, put it in an environment variable and name that variable in `api_key_env`.

The Sentinel inherits the `[model]` settings unless you override them. To give it a different (for example smaller) model or another server:

```toml
[sentinel]
base_url = "http://127.0.0.1:11434/v1"
name = "your-small-reviewer-model"
```

### Tests

The tests use the real configured model server and a real temporary SQLite database. Nothing is mocked. Some tests also fetch `https://example.com`, so they need internet access.

```powershell
.\.venv\Scripts\python -m pytest -q
```

### Demos

With `jig serve` running:

```powershell
.\.venv\Scripts\python scripts\demo.py --url http://127.0.0.1:8766       # goal -> plan -> approval -> done
.\.venv\Scripts\python scripts\chat_demo.py --url http://127.0.0.1:8766  # streaming chat with avatar states
```

## Safety model

Jig enforces safety at the tool level, in code. The prompt describes the rules but cannot loosen them. Every tool call passes through one gate, in this order:

1. **Schema.** The tool must exist and the arguments must match its JSON schema.
2. **Mode.** Every tool is tagged `read`, `private_write` or `side_effect`. In **research mode** (proactive background work, as in Dots), only `read` and `private_write` tools are offered to the model, and the gate refuses anything else even if the model tries it. So research mode can read permitted sources and write private notes or memories, but it cannot change files, send anything or take actions. **Action mode** has every tool, subject to the steps below.
3. **Core rules.** These are hard-coded and cannot be overridden (`GET /rules/core`). No credential or password changes. A secret may be used only by the tools on its allow-list. Sending a secret outbound always needs a human. Outbound tools may never reach localhost, the model server, Jig's own API or the local network, which also stops the agent from approving its own requests. Human-only tools (purchases, sending messages) always need approval. Core rules can only make a decision stricter.
4. **Custom rules.** Editable `allow`, `ask` or `block` rules per tool, with glob matching on tool names and, optionally, on an argument such as `url` matching `https://shop.*`. They are stored in SQLite and managed through `/rules`.
5. **The Sentinel.** Every outbound or side-effecting action is reviewed by an isolated model call with its own system prompt and no tools. It sees only the trusted intent, the proposed action and the policy findings, never the agent's conversation, so injected web content cannot address it. It returns a structured verdict of `allow`, `ask_user` or `deny`, with a risk level and a reason. A `deny` cannot be overridden. If the Sentinel fails or returns an invalid verdict, the action does not run and the error is reported.
6. **Approvals.** If a core rule, custom rule or the Sentinel asks for a human, an approval is queued (`/approvals`), the task's status becomes `waiting_approval`, and the run pauses. It resumes as soon as you approve or deny. Runs are checkpointed after every step, so a paused or interrupted task picks up where it left off after a restart.
7. **Vault.** Tools use secrets by reference (`{{secret:NAME}}`). The real value is inserted only at the moment the tool runs, after review, and any occurrence of it in the result is redacted before it reaches the model or the audit log. Values can be written through the API but never read back. Jig uses Windows DPAPI on Windows and the `keyring` library elsewhere, and refuses to start if neither is available.

**Audit.** Every model-call summary, tool call and result, policy decision, Sentinel verdict, approval, vault use, state change, and rule or memory change is appended to an audit log that SQLite triggers make append-only. Query it with `GET /audit?kind=tool&task_id=...`. Forgetting a memory deletes it from the table and the search index, and the audit log records only its id, never its content.

**Memory.** Memory is fully inspectable and editable: `GET /memory`, `GET /memory?q=...`, `POST /memory`, `PATCH /memory/{id}` and `DELETE /memory/{id}`, which really forgets it.

**Sandbox.** Every file tool is confined to `sandbox/<agent_id>/`. Absolute paths, drive letters, UNC paths, `..`, alternate data streams, reserved device names, and symlinks or junctions that lead out are all rejected. This is a directory jail, not an OS-level sandbox; a VM or container is on the roadmap.

## Avatar states

`/events` (WebSocket) and `/events/sse` stream every runtime event, including `avatar.state` events with `{state, variant, run_id, task_id}`. `GET /state` returns the current state. Names live in `jig/constants.py`:

`idle`, `sleeping` (background read-only work or active research schedules), `thinking`, `working` with a variant (`browsing`, `writing`, `coding`, `shopping`, `scheduling`), `talking`, `needs-approval`, `success` and `error`. The last two are shown for 3 seconds.

Each tool category maps to a variant: web to `browsing`, files to `writing`, time to `scheduling`, and so on. When several things happen at once, the precedence is needs-approval, then success/error, then talking, working, thinking, then sleeping, then idle.

## API

| Area | Endpoints |
| --- | --- |
| Health | `GET /health`, `GET /state`, `GET /tools` |
| Events | `WS /events`, `GET /events/sse`, `GET /events/recent?after=` |
| Chat | `POST /chat` (NDJSON stream: `start`, `reasoning`, `content`, `event`, `done` / `error`), `GET /sessions/{id}` |
| Goals | `POST /goals`, `GET /goals`, `GET /goals/{id}`, `POST /goals/{id}/cancel` |
| Tasks | `POST /tasks`, `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`, `GET /runs/{id}` |
| Schedules | `POST /schedules`, `GET /schedules`, `PATCH /schedules/{id}`, `DELETE /schedules/{id}` |
| Approvals | `GET /approvals?status=pending`, `GET /approvals/{id}`, `POST /approvals/{id}` `{approve, note}` |
| Rules | `GET/POST /rules`, `GET/PATCH/DELETE /rules/{id}`, `GET /rules/core` |
| Memory | `GET/POST /memory`, `GET/PATCH/DELETE /memory/{id}`, `GET /notes` |
| Audit | `GET /audit?kind=&task_id=&run_id=&after_id=&limit=` |
| Vault | `GET /vault` (names only), `PUT /vault/{name}`, `DELETE /vault/{name}` |

The API binds to `127.0.0.1` and does not yet require authentication; see the roadmap.

## Roadmap

- **VM or container sandbox** for files and code execution, replacing the directory jail.
- **Headless browser**, running inside the sandbox and gated by the Sentinel.
- **Voice**: local speech-to-text and text-to-speech, driving the avatar's `talking` state.
- **Messaging channels** such as email, Signal and Matrix, as human-only actions.
- **Vision**, once the model has a vision projector (for example an mmproj file for llama.cpp).
- **A smaller dedicated Sentinel model** for faster reviews (already configurable via `[sentinel]`).
- **MCP and plugin support**: third-party tools that declare their effect, outbound status and category.
- **Multiple agents**, each with its own sandbox, memory and rules.
- An API token for local clients, embeddings-backed memory search, and cron-style schedules.

## Licence

Apache License 2.0. See `LICENSE`.
