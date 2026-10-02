<p align="center">
  <a href="https://rlesueur.github.io/jig/"><img src="avatar/screenshots/jig-idle.png" alt="Jig, a neon mascot with a round dark head, glowing amber eyes, small horns and a flowing ribbon tail" width="260"></a>
</p>

<h1 align="center">Jig</h1>

<p align="center"><strong>An open-source, always-on personal AI agent that runs on your own local model.</strong></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-Apache--2.0-9a54ff" alt="Licence: Apache-2.0"></a>
  <a href="https://github.com/rlesueur/jig/actions/workflows/pages.yml"><img src="https://github.com/rlesueur/jig/actions/workflows/pages.yml/badge.svg" alt="GitHub Pages deployment status"></a>
  <img src="https://img.shields.io/badge/status-under%20active%20development-ffb246" alt="Status: under active development">
</p>

<p align="center">
  <a href="https://rlesueur.github.io/jig/"><strong>Website and live avatar</strong></a> ·
  <a href="https://rlesueur.github.io/jig/media/jig-promo.mp4">Watch the 28-second video</a> ·
  <a href="#running-it">Quick start</a> ·
  <a href="#run-with-docker">Docker</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

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
 │  Vision (optional image input)  Vault (DPAPI / keyring / keyfile)         │
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
| `jig/vault.py` | Credential vault (Windows DPAPI, or `keyring` elsewhere, or an explicit `[vault] backend`) |
| `jig/vault_backends/keyfile.py` | `keyfile` vault backend for containers: AES-256-GCM, scrypt, key from a file (Docker secret) |
| `jig/sandbox.py` | Per-agent workspace with path-traversal protection |
| `jig/sandbox_container/` | Container backend: Docker lifecycle, hardened container, egress relay and proxy, browser session, image (`image/Dockerfile`) |
| `jig/sandbox_compose/` | `compose` backend for container mode: talks to the long-running sandbox services over the internal network, start-up isolation checks, API guard |
| `jig/tools/sandbox_exec.py`, `jig/tools/browser.py` | `run_command` / `run_python` and the headless browser tools (container backend only) |
| `jig/vision.py` | Image content parts, the real vision probe, and image description for tools |
| `jig/events.py` | Event bus and the derived avatar state |
| `jig/constants.py` | All shared names: avatar states, task variants, modes and statuses |
| `jig/api/app.py` | HTTP and WebSocket API |
| `jig/autostart/` | Opt-in start at logon: Windows Task Scheduler (tested), launchd and systemd (untested), the launcher, CLI and API |
| `jig/instance.py`, `jig/lifecycle.py` | Single-instance lock per data directory, `jig stop`, graceful shutdown on logoff, rotating log files |
| `jig/model_server.py` | Optional `[model.launch]` supervision and the bounded readiness wait |
| `jig/power.py`, `jig/procinfo.py` | Turning Jig (and the model server it launched) off; what that frees on the GPU; re-adopting a server left running |
| `jig/remote.py`, `jig/devices.py` | Use Jig from your other devices: `tailscale serve`, the funnel guard, proving a request came through tailscaled, and paired device sessions |

## Running it

Requirements: Python 3.11 or newer, and a local OpenAI-compatible server with a tool-capable model.

```powershell
git clone https://github.com/rlesueur/jig.git
cd jig
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"

# Point jig.toml (or a profile) at your server, then:
.\.venv\Scripts\jig health                    # checks the endpoints and runs the capability probes
.\.venv\Scripts\jig serve                     # always-on agent and API on http://127.0.0.1:8766
.\.venv\Scripts\jig ui                        # opens the web UI, already signed in
.\.venv\Scripts\jig chat --url http://127.0.0.1:8766
```

### Web UI

`jig serve` also serves a dependency-free web UI at `http://127.0.0.1:8766/`. It has the live avatar (on the real `/events` stream), streaming chat with collapsed thinking, activity (goals, tasks and runs, with create, cancel, pause and resume), the approvals inbox with the Sentinel's verdict and reason, memory (list, search, edit, forget), the custom rules editor (core rules are read-only), the audit log and the model status. The avatar is served from `avatar/jig-avatar.js` in this repository, not copied, so the UI needs an editable install (`pip install -e .`) or a source checkout.

Run `jig ui` to open it signed in (see [Access and the API token](#access-and-the-api-token)). `jig ui --print-url` prints the one-time link instead of opening a browser.

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

The tests use the real configured model server and a real temporary SQLite database. Nothing is mocked. Some tests also fetch `https://example.com` and `https://httpbin.org`, so they need internet access. The container and browser tests (`tests/test_container_sandbox.py`, `tests/test_browser.py`) need Docker running and the sandbox image built (`jig sandbox build`). The vision tests need `[vision] enabled = true` and a vision-capable model. `tests/test_vault_keyfile.py` needs no model. `tests/test_compose_stack.py` runs against a live compose stack: start it, then set `JIG_STACK_URL` (for example `http://127.0.0.1:8766`) and run `pytest -m compose`. Without `JIG_STACK_URL` it is skipped.

```powershell
.\.venv\Scripts\python -m pytest -q
```

### Demos

With `jig serve` running:

```powershell
.\.venv\Scripts\python scripts\demo.py --url http://127.0.0.1:8766       # goal -> plan -> approval -> done
.\.venv\Scripts\python scripts\chat_demo.py --url http://127.0.0.1:8766  # streaming chat with avatar states
```

## Run with Docker

Jig also ships as a standalone container deployment: the images `ghcr.io/rlesueur/jig` and `ghcr.io/rlesueur/jig-sandbox` (linux/amd64 and linux/arm64), and `compose.yaml`. You need Docker and a model server. By default, that server runs on the host on port 8080. Full details are in [docs/container.md](docs/container.md).

```sh
openssl rand -base64 32 > secrets/jig_vault_key     # the vault key, once (PowerShell: see docs/container.md)
docker compose up -d                                # Jig, sandbox-exec and sandbox-browser
docker compose exec jig jig token show              # paste it into http://127.0.0.1:8766
```

Configuration comes from `deploy/jig.toml`, mounted read-only (or your own file, via `JIG_CONFIG_FILE`), and from variables in `.env` (see `deploy/env.example`). State lives in the named volume `jig-data`, and the agent's files in `workspace`. Jig waits up to 10 minutes for the model to be served, then runs the same capability checks as on the host (tool call and JSON schema, plus vision if enabled). If they fail, it exits with the reason and compose restarts it. It never picks another model.

**Profiles.**

- **Default:** a server on the host, at `host.docker.internal`, which also works on Linux.
- **`--profile ollama`:** Ollama with NVIDIA GPUs.
- **`--profile llamacpp`:** the official llama.cpp server image with GPUs and your `models/` folder.

With a profile, set `JIG_MODEL_BASE_URL` to `http://ollama:11434/v1` or `http://llamacpp:8080/v1`. Forks such as PrismML's llama.cpp (needed by some ternary models) are not in the official image, so keep those on the host.

**Security model in container mode.**

- **No Docker socket.** Jig runs as a non-root user with a read-only root filesystem and no capabilities, and does not get the Docker socket.
- **Loopback only.** The port is published on `127.0.0.1`.
- **Isolated sandbox.** Code and the browser run in the long-lived `sandbox-exec` and `sandbox-browser` services, with the same hardening as the per-agent container. Their only network is `internal: true`. The only way out is Jig's egress proxy, so leases, core rules, custom rules, the Sentinel and approvals all still apply. Jig refuses API requests from that network.
- **Checked at start-up.** Jig verifies all of this every time it starts, including that the sandbox cannot reach the internet directly, and refuses to start if anything is wrong.
- **One shared sandbox.** You get one sandbox for the stack instead of one container per agent. Run one compose project per agent, or Jig on the host, to get that back. [docs/container.md](docs/container.md#security-model) lists every trade-off.

**Vault key.** A container has no DPAPI and no keyring, so the container config selects `[vault] backend = "keyfile"`. Secrets are encrypted with AES-256-GCM under a key that scrypt derives from the Docker secret `jig_vault_key`. Without that file, or with a wrong key, Jig refuses to start and says how to fix it. It never generates a key and never stores secrets in plaintext. Back up the key separately from the data.

**Always on.** Every service has `restart: unless-stopped`. On Windows and macOS, set Docker Desktop to start when you sign in; on Linux, run `sudo systemctl enable --now docker`. The host autostart below (Task Scheduler, launchd, systemd) is not used in container mode.

**Updating and backups.** Update with `git pull`, `docker compose pull` and then `docker compose up -d`; volumes are kept. To back up, stop Jig and archive the `jig-data` volume:

```sh
docker compose stop jig
docker compose run --rm --no-deps -v "$PWD/backups:/backup" --entrypoint tar jig -czf /backup/jig-data.tgz -C /var/lib/jig/data .
docker compose start jig
```

## Running Jig always-on

Jig can start by itself when you log in, but **only if you turn this on**. It is never enabled by default, by an installer or by the agent. `jig autostart enable` first shows exactly what it will register: the command line, the trigger, the account it runs as, the log location and the settings. It registers nothing until you answer `y` (or pass `--yes` in a script). Turning it on or off is recorded in the audit log (`autostart.enabled`, `autostart.disabled`).

```powershell
.\.venv\Scripts\jig autostart show       # what would be registered, including the full task XML; changes nothing
.\.venv\Scripts\jig autostart enable     # shows the plan, asks y/N; --yes for scripts, --now to start it straight away
.\.venv\Scripts\jig autostart status     # registered or not, last run, last result, and whether Jig is running
.\.venv\Scripts\jig autostart disable    # removes everything enable registered
.\.venv\Scripts\jig stop                 # graceful shutdown of the Jig that uses this data directory
```

`--port` and `--data-dir` choose what the autostarted Jig uses (by default, the config's). The same actions are in the API, behind the usual token: `GET /autostart` (status and the plan), `POST /autostart/enable` with `{"confirm": true}` (anything else is refused) and `POST /autostart/disable`. The web UI has an Autostart row in the Status card; turning it on shows the same disclosure and needs your OK.

**What gets registered.**

| OS | Entry | Trigger | Runs as | Restarts | Status |
| --- | --- | --- | --- | --- | --- |
| Windows | Task Scheduler task `\Jig\Jig Agent` | at **your** logon, after 30 s | you, interactive token, not elevated; no password stored | the launcher retries a failed start 3 times, 60 s apart | tested on Windows 11 |
| macOS | `~/Library/LaunchAgents/io.github.rlesueur.jig.plist` | at login (`RunAtLoad`) | you | launchd, on a failed exit (`KeepAlive` / `SuccessfulExit = false`) | **untested** |
| Linux | `~/.config/systemd/user/jig.service` | when your user manager starts (login) | you (`systemd --user`) | `Restart=on-failure`, 60 s apart | **untested** |

On Windows the task runs `.venv\Scripts\pythonw.exe -m jig.autostart.launch --config ... --data-dir ... --port ...`, so there is no console window. It needs no administrator rights. Its settings: no execution time limit, start as soon as possible after a missed start, run on battery and keep running when unplugged, and one instance at a time. Task Scheduler's own restart-on-failure only covers a failure to launch: a non-zero exit code does not trigger it (we checked). So the small launcher runs `jig serve` and restarts it itself, but never after a clean stop, and never when another Jig already holds the data directory. The macOS and Linux backends generate the files above (their content is unit-tested) but have **not been run on a real Mac or Linux machine**. Please report what you find.

**Logs.** `<data_dir>/logs/jig.log` (Jig) and, on Windows, `autostart.log` (the launcher). Both rotate at 5 MB, keeping 5 files. A model server that Jig starts writes to `model-server.log`. `disable` leaves the logs in place.

**Starting the model server too (optional).** Jig is model-agnostic, so it starts nothing unless you ask it to. Add `[model.launch]` to have Jig start and supervise your server, whatever it is, and stop it again when Jig stops. Or leave out `command` and set only a readiness timeout, to wait for a server that runs as its own service, such as Ollama:

```toml
[model.launch]
command = "C:/path/to/llama-server.exe"      # or "ollama", with args = ["serve"]
args = ["-m", "C:/path/to/model.gguf", "--port", "8080", "--jinja"]
working_dir = ""                             # relative to the config file
readiness_timeout_s = 300                    # 0 (the default) checks once and fails at once
poll_interval_s = 2.0
max_restarts = 3                             # restarts of a launched server that exits on its own
restart_delay_s = 5.0
```

At start-up Jig polls `/v1/models` until the configured model is served, and logs its progress. If the endpoint is not ready in time, or the launched server exits, Jig exits with a clear error, and the launcher, launchd or systemd retries. This is a bounded wait, not a fallback: Jig never uses another server or model. `profiles/llamacpp-bonsai.toml` has a full example. Keep paths that are specific to your machine in an untracked config, such as `jig.local.toml` (gitignored), passed with `--config`.

If the configured endpoint is **already in use** when Jig starts (for example a server you started by hand):

- it serves the configured model: Jig uses it, launches nothing, and still runs the capability checks. It logs and audits `model server already running; not launching` (`model_server.already_running`). Jig does not supervise or stop a server it did not start;
- it answers HTTP 503 (llama.cpp while it loads): Jig launches nothing and waits for it, within `readiness_timeout_s`;
- anything else holds the port: Jig refuses to start and says so, because a second server could not bind the port.

A server that Jig launched itself is stopped when Jig stops (Ctrl+C, logoff, `jig stop --model`); plain `jig stop` leaves it running, as described in **Turning Jig off** below. If it exits on its own, Jig restarts it up to `max_restarts` times in a row (a run of 10 minutes or more resets the count), and audits each event (`model_server.exited`, `model_server.restarted`, `model_server.gave_up`).

In container mode (`deployment = "container"` or `JIG_DEPLOYMENT=container`, both set by the image and `deploy/jig.toml`), autostart does not apply: `jig autostart enable` refuses, `GET /autostart` returns `"applicable": false`, and the Status card explains that Docker keeps Jig running.

**Turning Jig off.** Because Jig may start at logon, you can turn it off at any time, from Settings in the web UI, the API or the command line. There are two choices:

| Choice | CLI | API | What stops |
| --- | --- | --- | --- |
| Turn Jig off | `jig stop` | `POST /power/stop` `{"scope": "jig", "confirm": true}` | Jig. A model server that Jig launched keeps running, and the next Jig start takes it back under supervision. |
| Turn Jig and the model off (frees the GPU) | `jig stop --model` | `POST /power/stop` `{"scope": "jig_and_model", "confirm": true}` | Jig and the model server, **only if Jig launched and supervises it** |

- **Graceful.** Running tasks are checkpointed and put back in the queue, exactly as on `jig stop` before, and nothing is left `running`. The API replies first (HTTP 202), then shuts down. Every request is audited (`power.stop`, with who asked, how and from where), and so is what happened to the model server (`model_server.left_running` or `model_server.stopped`).
- **Never someone else's process.** Jig stops a model server only if it launched it. It records that server (pid, process start time and command line) in `<data_dir>/model-server.json`, and a later Jig adopts it again only if the same process, matched on pid *and* start time, still serves the endpoint (`model_server.adopted`). If the server was started any other way (by hand, or as a service such as Ollama), `jig_and_model` is refused (HTTP 409) with an explanation and how to stop it yourself.
- **The confirmation is explicit.** `confirm` must be exactly `true`; anything else is refused (HTTP 400).
- **Autostart stays on.** Stopping does not unregister autostart, and the reply says so: if 'Start with Windows' is on, Jig starts again at your next logon. Turn that off in Settings or with `jig autostart disable` to keep it off. Without autostart, start Jig again with `jig serve`.
- **Container mode.** The API and `jig stop` refuse, because compose's restart policy (`unless-stopped`) would bring Jig straight back. Use `docker compose stop jig` (or `docker compose stop`) on the host instead, and `docker compose start` to start it again.

`GET /power` says whether Jig manages the model server, whether stopping it would free GPU memory and how Jig starts again. On NVIDIA GPUs it asks `nvidia-smi` for the memory that process holds. On Windows with WDDM drivers, `nvidia-smi` lists the process but does not report per-process memory, so `vram_mib` is `null` with a note, and the GPU totals are given instead. To stop or restart just the model server while Jig keeps running, use `jig model stop` (or `POST /model/stop` `{"confirm": true}`) and `jig model start` (`POST /model/start`). `jig model status` (`GET /model`) reports its state. All three apply only to a server Jig launched.

**Robustness.**

- **One Jig per data directory.** `<data_dir>/jig.lock` is held with an OS file lock for as long as Jig runs. A second Jig on the same data directory refuses to start and says who holds it. The OS releases the lock when the process exits, even after a crash.
- **Graceful shutdown** on `jig stop`, Ctrl+C, SIGTERM (launchd, systemd) and Windows logoff or shutdown. A windowless process gets no console events, so Jig handles `WM_ENDSESSION` through a hidden window and holds the logoff for up to 20 s while it stops. Running tasks are put back in the queue with their checkpoint (`task.interrupted` in the audit log) and resume on the next start; unfinished steps are marked `interrupted`. Nothing is left marked `running`. After a hard kill (`schtasks /End`, Task Manager, power loss), the next start does the same recovery.
- **Sleep, hibernation and clock changes.** If the gap between two heartbeats is over 60 s, the scheduler records `scheduler.clock_jump` with the gap and the overdue schedules. Each overdue schedule runs **once**, not once per missed interval, with `missed_runs` in its audit entry, and then carries on from now. If the clock went backwards, schedules that now look far in the future are brought back to one interval away.
- **Start-up reason.** `GET /status` and `GET /state` include `start_reason` (`manual` or `autostart`), and so does the `runtime.start` audit entry.

**Security notes.**

- Autostart runs Jig as you, with your normal rights, never elevated. It can do nothing at logon that it could not do when you start it by hand. The API still binds to `127.0.0.1` and needs the token.
- Only you can change the entry: the task, the LaunchAgent or the user unit belongs to your account. The command line points into your Jig folder and virtual environment. Anyone who can write there can already run code as you, but keep that folder private.
- Turn it off with `jig autostart disable` (or the Status card). It removes the task and its now-empty `\Jig` folder, the plist or the unit. If Jig is still running, stop it with `jig stop`.

**Boot before logon (not implemented).** On Windows, starting at boot would need a task that runs "whether the user is logged on or not", with your password stored in Task Scheduler and admin rights to register it. It would run without your interactive logon session. DPAPI can still work for a stored-credential (password) logon, but not for the S4U "do not store password" type, and it is easy to get wrong. A Windows service in session 0 would not have your profile at all. Jig therefore starts at logon only. On Linux, `loginctl enable-linger $USER` starts your user manager, and so Jig, at boot. Note that a desktop keyring is usually still locked until you log in, so vault access fails until then.

## Use Jig from your other devices

You can use the Jig on your computer from your phone, tablet or laptop, at home or away, through [Tailscale](https://tailscale.com). Jig itself keeps listening on `127.0.0.1` only. `tailscale serve` gives it an HTTPS address with a real certificate, `https://<machine>.<tailnet>.ts.net`, that only devices on **your tailnet** can reach, and forwards those requests to `http://127.0.0.1:8766`.

**Setting it up.** Jig never installs Tailscale or signs in for you. `jig remote status` (or the Settings page) checks `tailscale version` and `tailscale status --json` and lists exactly what is still missing:

1. Install Tailscale on this computer (Windows: [tailscale.com/download/windows](https://tailscale.com/download/windows) or `winget install Tailscale.Tailscale`) and sign in.
2. In the [admin console's DNS page](https://login.tailscale.com/admin/dns), turn on MagicDNS and HTTPS Certificates.
3. Install Tailscale on your other devices and sign in with the same account.
4. Turn on remote access: Settings > Use Jig from your other devices, or:

```powershell
.\.venv\Scripts\jig remote status      # Tailscale installed? signed in? the URL, problems and next steps
.\.venv\Scripts\jig remote enable      # shows what it exposes and to whom, asks y/N (--yes for scripts)
.\.venv\Scripts\jig remote disable     # removes Jig's tailscale serve entry
```

`enable` runs `tailscale serve --bg --https=443 http://127.0.0.1:<port>`, checks that Tailscale now shows that entry, and records the tailnet name, the origin and the login that turned it on in `<data_dir>/remote.json` (audited `remote.enabled`). It changes nothing else in your Tailscale settings. If port 443 on your tailnet name already serves something else, it refuses rather than replacing it. `disable` removes only Jig's entry (`tailscale serve --https=443 off`) and is audited too. The API equivalents are `GET /remote`, `POST /remote/enable` and `POST /remote/disable`, each with `{"confirm": true}`. Enabling works only from the host itself.

**After a restart.** Tailscale's documentation says that `tailscale serve --bg` entries persist until you turn them off, across restarts of tailscaled and of the computer. This has **not yet been verified on this machine**, because Tailscale is not installed here. Jig does not depend on it: every start compares `remote.json` with `tailscale serve status --json`. If remote access is on but Jig's entry has gone, it puts it back (`remote.restored`). If something else now uses that address, it leaves it alone, logs an error and reports it under `problems` in `GET /remote` (`remote.restore_failed`).

**Pairing a device.** Your other devices never get the master API token. On the host, Settings > Add a device shows a one-time **pairing code**: 8 characters, valid for 5 minutes, usable once. It also shows a QR code of the pairing URL `https://<machine>.<tailnet>.ts.net/#pair=<code>`, generated locally with no online service. On the new device, scan or open the link, check the code and name the device. It then gets its own **device session**: a random 256-bit secret in an `HttpOnly`, `SameSite=Strict`, `Secure` cookie. Jig keeps only a SHA-256 hash of it. Each device has a name, created and last-used times and an optional expiry (`expires_in_days`, 1 to 365). After five wrong codes in a row, every outstanding code is cancelled. API: `POST /devices/pairing` (host only), `POST /auth/pair` `{code, name}` (on the new device), `GET /devices`, `DELETE /devices/{id}`.

**Revoking.** Revoke a lost or old device in Settings (`DELETE /devices/{id}`), and its cookie stops working at once. Signing out on a device unpairs it. Rotating the master token (`jig token rotate`, or `POST /auth/token/rotate` with `{"confirm": true}` on the host) revokes **every** device and signs out every browser, because each device session is bound to the current token.

**Security notes: what is exposed, and to whom.**

- **Exposed:** the same web UI and API as on `127.0.0.1`, at one HTTPS address on your tailnet. Nothing else on the computer is exposed, and Jig still binds to `127.0.0.1` only.
- **To whom:** Tailscale lets only devices on your tailnet connect (subject to your tailnet's access rules). Jig then also requires all of the following:
  1. **The connection really comes from tailscaled.** The request must arrive on a TCP connection from `127.0.0.1` that belongs to the running Tailscale service. On Windows, Jig looks up the connection's owning process (`GetExtendedTcpTable`) and compares it with the Tailscale service's pid. Any local program could send `Host: <machine>.ts.net` and fake `Tailscale-User-Login` headers, so those headers mean nothing unless this check passes. On a normal local connection Jig removes them before any route sees them. Uvicorn's proxy-header handling is off, so `X-Forwarded-For` can never replace the real peer. On Linux the check compares socket owners (UNTESTED); other systems are refused.
  2. **An allowed Tailscale user.** tailscaled deletes any `Tailscale-User-*` header the client sent and adds the real tailnet user. Jig accepts only the login that turned remote access on, or the exact logins in `[remote] allowed_logins`, with no wildcards. Tagged devices, which have no user, are refused. A device paired over the tailnet is also bound to the login that paired it.
  3. **A paired device session.** Over the tailnet, the master token (`Authorization: Bearer`) and browser sign-in with the token or a login code are refused. Only a device session works. Adding devices, turning remote access on and rotating the token all have to be done on the host.
- **Origins.** Cookie-authenticated requests that change something, and every WebSocket handshake, must send exactly `Origin: https://<machine>.<tailnet>.ts.net`, the value recorded when remote access was turned on, or the local origin for local requests. There are no wildcards: `http://` and lookalike or other `.ts.net` names are refused, and any `.ts.net` name other than the recorded one is refused outright.
- **Never Tailscale Funnel.** Funnel would put Jig on the public internet. Jig refuses to start if `tailscale serve status --json` shows a funnel to its port. `jig remote enable` refuses as well, and every request that tailscaled marks as funnelled (`Tailscale-Funnel-Request`) is rejected. Do not run `tailscale funnel` for Jig's port.
- **In container mode**, see [docs/container.md](docs/container.md#use-jig-from-your-other-devices-tailscale): Jig cannot see which process owns a connection, so the tailnet name is configured explicitly and a paired device session is what grants access.

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
7. **Vault.** Tools use secrets by reference (`{{secret:NAME}}`). The real value is inserted only at the moment the tool runs, after review, and any occurrence of it in the result is redacted before it reaches the model or the audit log. Values can be written through the API but never read back. Jig uses Windows DPAPI on Windows and the `keyring` library elsewhere, and refuses to start if neither is available. In containers, `[vault] backend = "keyfile"` uses a key you supply as a Docker secret (see [Run with Docker](#run-with-docker)).

**Audit.** Every model-call summary, tool call and result, policy decision, Sentinel verdict, approval, vault use, state change, and rule or memory change is appended to an audit log that SQLite triggers make append-only. Query it with `GET /audit?kind=tool&task_id=...`. Forgetting a memory deletes it from the table and the search index, and the audit log records only its id, never its content.

**Memory.** Memory is fully inspectable and editable: `GET /memory`, `GET /memory?q=...`, `POST /memory`, `PATCH /memory/{id}` and `DELETE /memory/{id}`, which really forgets it.

**Sandbox.** Every file tool is confined to `sandbox/<agent_id>/`. Absolute paths, drive letters, UNC paths, `..`, alternate data streams, reserved device names, and symlinks or junctions that lead out are all rejected. This is a directory jail, not an OS-level sandbox. Code execution and the browser exist only with the container backend (see above), which adds OS-level isolation and gated egress.

## Avatar states

`/events` (WebSocket) and `/events/sse` stream every runtime event, including `avatar.state` events with `{state, variant, background, run_id, task_id}`. `GET /state` returns the current state. The names are the avatar's own (`avatar/README.md`) and live in `jig/constants.py`:

`idle`, `monitoring` (active research schedules, nothing running), `thinking`, `working` with a variant (`browsing`, `writing`, `coding`, `shopping`, `scheduling`), `talking`, `approval`, `paused` (the agent or a task is paused), `success` and `error`. The last two are shown for 3 seconds.

Each tool category maps to a variant: web to `browsing`, files to `writing`, time to `scheduling`, and so on. Proactive read-only research is reported as `working` / `browsing` with `background: true`, which the avatar draws dimmed, half-lidded and slower (`setState('working', {task: 'browsing', background: true})`). When several things happen at once, the precedence is approval, then success/error, then foreground talking, working, thinking, then paused, then background working, then monitoring, then idle.

### Pause and resume

`POST /tasks/{id}/pause` pauses a task and `POST /tasks/{id}/resume` puts it back in the queue; it continues from its last checkpoint and reuses any approval already given. A pause takes effect at the next safe point: between steps, during a model call or while waiting for an approval. A tool that is already running is allowed to finish first. `POST /agent/pause` pauses the whole agent (it survives restarts): schedules stop firing, no task starts, and running tasks are interrupted and re-queued. Chat still works. `POST /agent/resume` undoes it, and `GET /agent` reports it.

## API

| Area | Endpoints |
| --- | --- |
| Health | `GET /health` (public, `{"status": "ok"}` only), `GET /status`, `GET /state`, `GET /tools` |
| Auth | `POST /auth/login-code`, `GET/POST /auth/session`, `POST /auth/logout`, `POST /auth/pair` `{code, name}` (public, a pairing code is the credential), `POST /auth/token/rotate` `{confirm: true}` (host only; revokes every device) |
| Agent | `GET /agent`, `POST /agent/pause`, `POST /agent/resume` |
| Events | `WS /events`, `GET /events/sse`, `GET /events/recent?after=` |
| Chat | `POST /chat` (NDJSON stream: `start`, `reasoning`, `content`, `event`, `done` / `error`), `GET /sessions/{id}` |
| Goals | `POST /goals`, `GET /goals`, `GET /goals/{id}`, `POST /goals/{id}/cancel` |
| Tasks | `POST /tasks`, `GET /tasks?newest_first=&limit=`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`, `POST /tasks/{id}/pause`, `POST /tasks/{id}/resume`, `GET /runs?task_id=&kind=`, `GET /runs/{id}` |
| Schedules | `POST /schedules`, `GET /schedules`, `PATCH /schedules/{id}`, `DELETE /schedules/{id}` |
| Approvals | `GET /approvals?status=pending`, `GET /approvals/{id}`, `POST /approvals/{id}` `{approve, note}` |
| Rules | `GET/POST /rules`, `GET/PATCH/DELETE /rules/{id}`, `GET /rules/core` |
| Memory | `GET/POST /memory`, `GET/PATCH/DELETE /memory/{id}`, `GET /notes` |
| Audit | `GET /audit?kind=&task_id=&run_id=&after_id=&before_id=&newest_first=&limit=` |
| Vault | `GET /vault` (names only), `PUT /vault/{name}`, `DELETE /vault/{name}` |
| Autostart | `GET /autostart`, `POST /autostart/enable` `{confirm: true, start_now}`, `POST /autostart/disable` |
| Power | `GET /power`, `POST /power/stop` `{scope: "jig" \| "jig_and_model", confirm: true}` (202, then a graceful shutdown) |
| Model server | `GET /model`, `POST /model/stop` `{confirm: true}`, `POST /model/start` (only a server Jig launched) |
| Remote access | `GET /remote`, `POST /remote/enable` `{confirm: true}` (host only), `POST /remote/disable` `{confirm: true}` |
| Devices | `GET /devices`, `POST /devices/pairing` `{expires_in_days}` (host only; code, link and QR code), `DELETE /devices/{id}` |

### Access and the API token

The API binds to `127.0.0.1`, and every endpoint except `GET /health` and the UI's static files needs authentication, including `/events`, `/events/sse` and `/approvals`.

- **The token.** On first start Jig generates a random 256-bit token and saves it as `<data_dir>/api-token`. On Windows the file's ACL is cut down to your own account (inheritance removed) and then read back and checked; on other systems it is mode `0600`. If the permissions cannot be set or are looser than that, Jig refuses to start. `jig token show` prints it and `jig token rotate` replaces it; a rotation takes effect at once, signs out every browser session and revokes every paired device.
- **Programs** send `Authorization: Bearer <token>`, on the WebSocket handshake too. `jig chat`, `jig ui` and the scripts in `scripts/` read the token file themselves.
- **The browser** never holds the token. `jig ui` asks the API for a one-time login code (valid for 2 minutes, single use) and opens `http://127.0.0.1:8766/#code=...`. The code is in the URL fragment, which the browser never sends to the server, so it does not appear in logs. The page exchanges it for an `HttpOnly`, `SameSite=Strict` session cookie (an HMAC of the token with a 12-hour expiry) and removes the fragment from the address bar. You can also paste the token into the sign-in dialog. Requests that use the cookie and change something, and WebSocket handshakes that use it, must come from the UI's own origin, which stops other sites and pages from driving the API.
- **Other devices** (over Tailscale, see [Use Jig from your other devices](#use-jig-from-your-other-devices)) use a paired device session instead: a separate, revocable cookie with a `Secure` flag. The bearer token and the session cookie are refused there.
- **WebSockets** are authenticated during the handshake, either by the bearer header or by a cookie plus the `Origin` check. The token is never put in a query string. A rejected handshake is closed with code 1008.
- **`/health` is public** so supervisors and container health checks can probe Jig without a secret. It returns only `{"status": "ok"}`. The model endpoint, model name and capability results are under `GET /status`, which needs authentication.

## Roadmap

- **A VM sandbox** (for example Firecracker or Hyper-V) as a stronger alternative to the container backend, and per-task egress leases.
- **Voice**: local speech-to-text and text-to-speech, driving the avatar's `talking` state.
- **Messaging channels** such as email, Signal and Matrix, as human-only actions.
- **A smaller dedicated Sentinel model** for faster reviews (already configurable via `[sentinel]`).
- **MCP and plugin support**: third-party tools that declare their effect, outbound status and category.
- **Multiple agents**, each with its own sandbox, memory and rules.
- Embeddings-backed memory search and cron-style schedules.

## Licence

Apache License 2.0. See `LICENSE`.
