# Jig

Jig is an open-source, always-on personal AI agent that runs **only on local models**. It works with any sufficiently capable model behind an OpenAI-compatible endpoint that supports tool calling, such as llama.cpp (including forks), Ollama, LM Studio or vLLM. Your memory, audit trail, rules and secrets stay on your machine, in files you can inspect and edit.

Jig is an open alternative to hosted agents such as Meta's Muse and OpenAI's Dots. It takes their best safety ideas (an isolated reviewer, a credential vault, read-only background research, per-action rules and approvals) and adds the things that running locally makes possible: memory you can see and edit, and an audit trail you own.

> Status: foundations. The runtime, safety model, API, tests, an optional container sandbox with a headless browser, and vision are in place. Voice and messaging channels are on the roadmap.

## Model requirements

Jig is model-agnostic. Nothing in the code assumes a particular model, prompt format or sampling scheme: the endpoint, model name and sampling parameters all come from config. Jig needs:

- **Native tool calling** through the OpenAI `tools` / `tool_calls` API, including well-formed JSON arguments. Parallel tool calls are used when the model offers them.
- **JSON-schema structured output** (`response_format: {type: "json_schema"}`) for the planner and the Sentinel.
- **A context of 32K tokens or more** (advisable). Jig logs a warning at start-up if the server reports less.
- **Decent instruction following**, so it can plan, use tools sensibly and give honest answers when a tool is refused.

Reasoning text (`reasoning_content` or `reasoning`) is optional. When a server sends it, Jig records it in the run steps. When it doesn't, nothing breaks.

At start-up Jig runs a **real capability check** against the configured model. It requests a specific tool call and checks the name and arguments, and it requests a JSON-schema answer and checks the value. If either check fails, Jig refuses to start and explains why. Run the same checks any time with `jig health`.

Jig was developed and tested with **Ternary Bonsai 2 27B** on the PrismML llama.cpp fork (`profiles/llamacpp-bonsai.toml`). That is one example setup, not a requirement.

### Vision (optional)

Vision is optional and works with any vision-capable model. Images are sent as standard OpenAI `image_url` content parts (base64 data URLs). Turn it on with:

```toml
[vision]
enabled = true
```

With vision enabled, the start-up check (and `jig health`) also sends a **real test image**: a square in a randomly chosen colour. The model has to name the colour, or Jig refuses to start. Tools that need vision, such as `browser_screenshot` with a question, raise `VisionUnavailable` with a clear message if vision is off; they never quietly carry on without the image. Without vision, use `browser_read` for the page text.

How to serve a vision model:

- **llama.cpp** (and forks): download the model's vision projector (`mmproj-*.gguf`) and add `--mmproj <file>` to `llama-server`. For the test model that is `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` (about 0.63 GB) from `prism-ml/Ternary-Bonsai-2-27B-gguf`:
  `llama-server -m Ternary-Bonsai-2-27B-PTQ1_0.gguf --mmproj Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf --jinja ...`
- **Ollama** and **LM Studio** ship their own vision models (for example Gemma 3 or Qwen2.5-VL); pick one of those as `name`. Their OpenAI-compatible endpoints accept the same image parts.
- **vLLM** serves multimodal models directly.

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
 │         (container backend) run_command · run_python · browser_*         │
 │  Sandbox: per-agent folder, or a hardened Docker container ──► egress proxy (lease + core rules)
 │  Vision (optional image input)  Vault (DPAPI / keyring)                   │
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
| `jig/sandbox_container/` | Container backend: Docker lifecycle, hardened container, egress relay and proxy, browser session, image (`image/Dockerfile`) |
| `jig/tools/sandbox_exec.py`, `jig/tools/browser.py` | `run_command` / `run_python` and the headless browser tools (container backend only) |
| `jig/vision.py` | Image content parts, the real vision probe, and image description for tools |
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

The tests use the real configured model server and a real temporary SQLite database. Nothing is mocked. Some tests also fetch `https://example.com` and `https://httpbin.org`, so they need internet access. The container and browser tests (`tests/test_container_sandbox.py`, `tests/test_browser.py`) need Docker running and the sandbox image built (`jig sandbox build`). The vision tests need `[vision] enabled = true` and a vision-capable model.

```powershell
.\.venv\Scripts\python -m pytest -q
```

### Demos

With `jig serve` running:

```powershell
.\.venv\Scripts\python scripts\demo.py --url http://127.0.0.1:8766       # goal -> plan -> approval -> done
.\.venv\Scripts\python scripts\chat_demo.py --url http://127.0.0.1:8766  # streaming chat with avatar states
```

## Container sandbox and headless browser

By default (`[sandbox] backend = "directory"`), the file tools are confined to a folder and Jig cannot run code or use a browser. To run code, shell commands and a headless Chromium in an isolated Linux container, use the container backend:

```powershell
.\.venv\Scripts\jig sandbox build      # docker build -t jig-sandbox:0.1.0 jig/sandbox_container/image
```

```toml
[sandbox]
backend = "container"     # or set JIG_SANDBOX_BACKEND=container
image = "jig-sandbox:0.1.0"
cpus = 2.0
memory = "2g"
pids_limit = 512
egress_ports = [80, 443]
```

If the container backend is selected and Docker is not running, or the image has not been built, Jig refuses to start and says why. It never falls back to the directory sandbox. The image is based on `mcr.microsoft.com/playwright/python` (Chromium included) and adds a pinned `playwright` package, an unprivileged `jig` user (uid 10001) and two small scripts: the browser server and the egress relay.

**The container.** Each agent workspace gets its own container (`jig-sbx-<agent>-<hash>`). It is created at start-up and removed at shutdown; files persist in the workspace. It runs with:

- only the agent's workspace bind-mounted, at `/workspace`; no other host path is visible;
- user `10001:10001` (non-root), `--cap-drop ALL` and `--security-opt no-new-privileges`;
- `--read-only` root filesystem, with tmpfs at `/tmp` and `/home/jig` (`nosuid,nodev`) and a 256 MB `/dev/shm`;
- `--memory 2g --memory-swap 2g`, `--cpus 2` and `--pids-limit 512` (all configurable); `--init` reaps stray processes;
- Docker's default seccomp profile; every command is wrapped in `timeout -s KILL`, so it also dies inside the container.

**Egress.** The container's only network is an `--internal` Docker network with no gateway, and external DNS is disabled (`--dns 127.0.0.1`). Its one neighbour is a relay container (`jig-egress-<...>`, hardened in the same way). The relay forwards every TCP connection to the **egress proxy inside the Jig runtime** on the host (an ephemeral port on `egress_bind`, reached through `host.docker.internal`) and to nowhere else. `HTTP(S)_PROXY` and Chromium's proxy setting point at the relay. The proxy decides on every connection, whether `CONNECT` for HTTPS or an absolute-URI request for HTTP:

1. **Lease.** Egress is open only while a container tool that has already passed the whole gate (core rules, custom rules, the Sentinel and any approval) is running. `run_command`, `run_python`, `browser_open`, `browser_click`, `browser_type`, `browser_fill`, `browser_submit` and `browser_login` open a lease; `browser_read` and `browser_screenshot` do not. Background processes and page scripts get no network between reviewed actions.
2. **Core rule `no-local-network`.** The proxy resolves the host itself and refuses loopback, private, link-local and reserved addresses, which covers Jig's API, the model server and the LAN. It then connects to the exact address it checked, so DNS rebinding cannot swap it afterwards.
3. **Ports.** Only `egress_ports` are allowed (80 and 443 by default).
4. **Custom rules** on the pseudo-tool `egress`, matching `host` or `url`, for example `{"tool": "egress", "arg": "host", "pattern": "*.tracker.com", "decision": "block"}`. An `ask` rule refuses the connection too, because a connection cannot wait for a human.

Every decision is audited as `egress.allow` or `egress.block`, with the host, port, tool and run.

**Tools (container backend).**

| Tool | Effect | Gate |
| --- | --- | --- |
| `run_command`, `run_python` | side effect, outbound | Sentinel; **asks for approval by default** (add an `allow` rule to relax it) |
| `browser_open` | read, outbound | allowed in research mode; Sentinel-reviewed like `web_fetch`; core rules on the URL |
| `browser_read` (text or accessibility snapshot), `browser_screenshot` | read | allowed in research mode; no network |
| `browser_click`, `browser_type`, `browser_fill` | side effect, outbound | action mode only; Sentinel; approval if a rule or the Sentinel asks |
| `browser_submit`, `browser_login` | side effect, outbound, **human-only** | always needs your approval |

Inside the browser, a click or the Enter key cannot submit a form. Every non-GET request (form posts, fetch or XHR writes, beacons) is aborted at the network layer, and `submit` events are cancelled, unless `browser_submit` or `browser_login` (both approved) is running. The tool result then says that a submission was blocked. `browser_login` takes the password only as a vault reference (`{{secret:NAME}}`); a literal password fails schema validation before anything runs. The values are inserted after approval, redacted from that result by the gate, and redacted by the browser from every later reply. `browser_screenshot` saves a PNG under `screenshots/` in the workspace. Given a question, it shows the image to the model, which needs vision; without vision it fails loudly. All browser tools show the avatar as `working` / `browsing`, and `run_command` / `run_python` as `working` / `coding`.

**Limits of this design.** Read these before you rely on it.

- The Sentinel reviews the **action** (the URL, command or selector), not each connection. During an approved action, the code or page can reach any public host on the allowed ports. The proxy enforces the core rules and your `egress` rules, but it cannot judge intent per request. Encrypted (CONNECT) traffic is not inspected.
- Leases are per runtime, not per task. While one task's approved action runs, a background process left behind by another task in the same container could also get out.
- Only TCP through the proxy is possible: no UDP, ICMP or direct DNS. Docker's embedded DNS still resolves the relay's name.
- A container is not a VM. It shares the kernel (on Docker Desktop, the WSL 2 or Hyper-V VM's kernel). A kernel exploit could escape it. Chromium runs without its own sandbox (`--no-sandbox`), because user namespaces are not available with all capabilities dropped; the container is the boundary.
- Page scripts can change the in-page submit guard, which can let GET forms through. That is no worse than following a link. The network-level block on non-GET requests is the real enforcement, and page scripts cannot touch it.
- File tools (`read_file`, `write_file`, ...) still use the directory jail on the host, on the same folder that is mounted at `/workspace`.
- On Linux Docker Engine (not Desktop), `host.docker.internal` resolves to the bridge gateway, so set `egress_bind` to an address on that bridge (for example `172.17.0.1`). Start-up runs an end-to-end check of the relay path and refuses to start if it fails.

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

**Sandbox.** Every file tool is confined to `sandbox/<agent_id>/`. Absolute paths, drive letters, UNC paths, `..`, alternate data streams, reserved device names, and symlinks or junctions that lead out are all rejected. This is a directory jail, not an OS-level sandbox. Code execution and the browser exist only with the container backend (see above), which adds OS-level isolation and gated egress.

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

- **A VM sandbox** (for example Firecracker or Hyper-V) as a stronger alternative to the container backend, and per-task egress leases.
- **Voice**: local speech-to-text and text-to-speech, driving the avatar's `talking` state.
- **Messaging channels** such as email, Signal and Matrix, as human-only actions.
- **A smaller dedicated Sentinel model** for faster reviews (already configurable via `[sentinel]`).
- **MCP and plugin support**: third-party tools that declare their effect, outbound status and category.
- **Multiple agents**, each with its own sandbox, memory and rules.
- An API token for local clients, embeddings-backed memory search, and cron-style schedules.

## Licence

Apache License 2.0. See `LICENSE`.
