<p align="center">
  <a href="https://rlesueur.github.io/jig/"><img src="avatar/screenshots/jig-idle.png" alt="Jig, a neon mascot with a round dark head, soft leaf ears, big round amber eyes and a flowing ribbon tail" width="260"></a>
</p>

<h1 align="center">Jig</h1>

<p align="center"><strong>An open-source, always-on personal AI agent. Built for local models. Bring a cloud model if you want one.</strong></p>

<p align="center">This release is a <strong>beta</strong> (version 0.1.0b1).</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-Apache--2.0-9a54ff" alt="Licence: Apache-2.0"></a>
  <a href="https://github.com/rlesueur/jig/actions/workflows/pages.yml"><img src="https://github.com/rlesueur/jig/actions/workflows/pages.yml/badge.svg" alt="GitHub Pages deployment status"></a>
  <img src="https://img.shields.io/badge/release-beta-ffb246" alt="Release: beta">
</p>

<p align="center">
  <a href="https://rlesueur.github.io/jig/"><strong>Website and live avatar</strong></a> ·
  <a href="https://rlesueur.github.io/jig/media/jig-promo.mp4">Watch the 47-second video</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="docs/README.md">Guides</a> ·
  <a href="#run-with-docker">Docker</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

Jig is an open-source, always-on personal AI agent, **built for local models**. It works with any sufficiently capable model behind an OpenAI-compatible endpoint that supports tool calling, such as llama.cpp (including forks), Ollama, LM Studio or vLLM. Your memory, audit trail, rules and secrets stay on your machine, in files you can inspect and edit. If you want a cloud model instead (OpenAI, OpenRouter, Anthropic or Gemini), you can bring one, with your explicit consent: see [Using a cloud model](#using-a-cloud-model).

Jig is an open, local alternative to hosted always-on agents such as Meta's Muse and OpenAI's Dots. Its safety model is an isolated safety checker, a credential vault, read-only background research, per-action rules and approvals. Because it runs on your machine, its memory is yours to see, edit and wipe, and its audit trail is yours too.

> Status: foundations. The runtime, safety model, API, tests, an optional container sandbox with a headless browser, vision, chat file attachments (PNG, JPEG, Word, PDF, text and Markdown), a payment and booking checkpoint for the browser, connectors for Gmail, Google Calendar, Google Drive, Outlook calendar and OneDrive, GitHub, Discord and WhatsApp, and MCP servers you add yourself are in place. Voice is on the roadmap; see [Connectors](#connectors) for exactly what is built and tested.

## Get started

Jig needs two things: Jig itself, and a model to think with. The model can run on your own computer, in a free app such as LM Studio or Ollama, or you can use a paid cloud model with an API key. Jig's set-up page helps you choose, and checks your choice works before Jig starts.

> **Don't run `pip install jig`.** That name on PyPI belongs to an unrelated project ("check your code before you git commit"). Use the installer below, or install from this repository.

### On Windows: the installer

1. Download `JigSetup-<version>.exe` from the [v0.1.0b1 release](https://github.com/rlesueur/jig/releases/tag/v0.1.0b1).
2. Open it. **Windows will probably warn you**, because the installer isn't code-signed (a signing certificate is expensive and Jig is a free project):
   - your browser may say the file "isn't commonly downloaded": choose **Keep**;
   - Windows may show **"Windows protected your PC"** (SmartScreen): click **More info**, then **Run anyway**.

   Only do this for a file you downloaded from this repository's releases page.
3. Click through the installer. It needs no administrator rights and no Python, Git or terminal. It asks whether Jig should start when you sign in to Windows (off unless you tick it).
4. At the end, Jig opens in its own window on its set-up page. Choose a model there (see [Get a model running](#get-a-model-running) if you don't have one yet), and Jig checks it and starts.

Afterwards, open Jig from the Start menu, or from its icon by the clock, which also shows whether Jig is on and can turn it off and on. Closing Jig's window leaves Jig running, so schedules still run and the icon by the clock says when a job is waiting for your OK; to turn Jig off, use **Turn Jig off** on that icon. Jig keeps its settings, memories and notes in `%LOCALAPPDATA%\Jig`. To remove it, use **Settings > Apps > Installed apps > Jig > Uninstall**: it turns Jig off, removes Start with Windows, and asks whether to keep your data.

### Get a model running

If you already run a model app, skip this: Jig's set-up page finds llama.cpp (port 8080), LM Studio (port 1234) and Ollama (port 11434) by itself, or you can type its address.

Otherwise, install one of these free apps and load **Granite 4.2 8B** (the `Q4_K_M` version, a 5.3 GB download). It's the model Jig has measured on small graphics cards:

| Graphics card memory | What to load | Measured |
| --- | --- | --- |
| 12 GB or more | Granite 4.2 8B Q4_K_M, context length 32768 | about 10.6 GB |
| 10 to 12 GB | Granite 4.2 8B Q4_K_M, context length 16384 (Jig works best with 32K) | about 8.1 GB |
| Less than 10 GB, or no graphics card | Nothing measured yet: use a [cloud model](#using-a-cloud-model) | |

Those figures were measured with llama.cpp's `llama-server` on one card, as the change in total graphics memory, so allow some margin. LM Studio and Ollama load the same kind of file, but we haven't measured them. Jig's set-up page reads your graphics card and makes the same suggestion.

- **LM Studio** ([lmstudio.ai](https://lmstudio.ai/)): search for `granite-4.2-8b` and download the `Q4_K_M` version from lmstudio-community. Load it with **Context Length** set as in the table, then start the server in the **Developer** tab.
- **Ollama** ([ollama.com](https://ollama.com/)): in the Ollama app's settings, set **Context length** to 32k (or 16k), then run `ollama pull granite4.2:8b-q4_K_M` in a terminal. Ollama uses a small context unless you change this, and Jig needs room.
- **llama.cpp**: `llama-server -m granite-4.2-8b-Q4_K_M.gguf -ngl 99 --jinja -c 32768 --port 8080` (file from [lmstudio-community/granite-4.2-8b-GGUF](https://huggingface.co/lmstudio-community/granite-4.2-8b-GGUF)).

Whatever you choose, Jig won't start its agent until the model passes real checks (it must call tools correctly and answer in Jig's format). It never falls back to another model.

### On Mac, Linux, or Windows without the installer

You need [Python](https://www.python.org/downloads/) 3.11 or newer and [Git](https://git-scm.com/downloads). In a terminal:

```powershell
git clone https://github.com/rlesueur/jig.git
cd jig
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jig serve
```

On Mac and Linux, use `.venv/bin/` instead of `.\.venv\Scripts\`. `jig serve` opens Jig, already signed in: in its own window on Windows, and in your browser on Mac and Linux (add `--browser` to use the browser on Windows too). If its model passes Jig's checks, the agent starts straight away: in a fresh checkout `jig.toml` points at llama.cpp on `http://127.0.0.1:8080/v1`, so a running `llama-server` there is enough. Otherwise Jig opens on its set-up page, where you choose a model. **Leave that terminal window open**: closing it stops Jig. To stop Jig, press Ctrl+C there, or run `jig stop` in another window. To open Jig again while it's running, run `jig ui`.

### Once Jig is running

Everything is in Jig's window (or web page), with no terminal or config file: change the model, add or remove a cloud key, let Jig run code (with Docker), start Jig with Windows, connect your accounts and use Jig from your phone are all in **Settings**. Try one of the example prompts on the chat screen to start.

### Jig's window on Windows

On Windows, Jig opens in a window of its own (titled Jig, with Jig's icon and its own button on the taskbar) instead of a browser tab: from the installer, the Start menu, the icon by the clock, `jig serve` and `jig ui`. It's Jig's usual web page, drawn by Microsoft Edge WebView2, the browser engine that comes with Windows 11 (and that Edge installs on Windows 10), through [pywebview](https://pywebview.flowrl.com/). Without WebView2, Jig says so and offers to get it (free, from Microsoft) or to open Jig in your browser instead; it never uses the old Internet Explorer engine.

- It signs in by itself with a one-time code, as `jig ui` does, so there's no token to type, and signs in again if its 12-hour session ends while it's open.
- There's one window per copy of Jig: opening Jig again brings it to the front. It remembers its size and position (`window.json` in the data folder).
- Links to other sites, such as a provider's sign-in page or these docs, open in your usual browser, so connecting an account (including GitHub's device code and Google and Microsoft sign-ins) works as before.
- **Open in browser** (the arrow at the top right of the window, and on the icon by the clock) opens Jig in your browser instead. The browser is also how you use Jig from another device (see [Use Jig from your other devices](#use-jig-from-your-other-devices)). On Mac and Linux, Jig opens in the browser.
- Closing the window doesn't turn Jig off. When a job is waiting for your OK, the icon by the clock shows a Windows notification. It only says that a job is waiting, not what it wants to do, so nothing private shows on the lock screen. Click it to open Jig.

### Timezone

Schedules and the agent's sense of the time use Jig's timezone. By default that's your computer's timezone, read from Windows, macOS or Linux (or the `TZ` environment variable) each time Jig starts. To choose another, use **Settings > Schedules > Timezone**, which also moves schedules that were on the old timezone so they keep their times of day, or set `timezone = "America/New_York"` (any IANA name) under `[runtime]` in `jig.toml`; Settings wins. If Jig can't tell your computer's timezone, it says so and won't start until you set one. It never guesses. In the container, set `TZ` in `.env`; without it the container's timezone is UTC.

## If something goes wrong

Jig explains problems in plain English on its set-up page, in Settings and in the terminal. The common ones:

- **"Jig couldn't reach your model."** Your model app isn't running, or its server isn't started (in LM Studio, start it in the Developer tab), or it uses another address: type it on the set-up page. If Jig started before your model app (at sign-in, say), it keeps trying by itself and starts as soon as the model answers.
- **"This model can't do everything Jig needs."** The model didn't call a tool correctly or didn't answer in Jig's format. Choose a model that supports tool calling, such as the one above.
- **"That model isn't loaded"** or **"Which model should Jig use?"** Load the model in your model app, or choose one of those it lists.
- **"The API key wasn't accepted."** Copy the key again from the provider's website and paste it into Jig's model settings.
- **Context too small.** Jig warns if the model has less than 32K of context. Raise the context length in your model app (see the table above).
- **"Jig can't start on port 8766"**: another program uses that port. Close it, or start Jig with `jig serve --port 8767`.
- **"Jig is already running for this data folder."** Open it with `jig ui`, or from the Start menu if you used the installer.
- **The page says Jig isn't running.** Start it again from the Start menu or its icon by the clock, or with `jig serve`.

Jig's log is in its data folder, in `logs\jig.log`.

## Model requirements

Jig is model-agnostic. Nothing in the code assumes a particular model, prompt format or sampling scheme: the endpoint, model name and sampling parameters all come from config. Jig needs:

- **Native tool calling** through the OpenAI `tools` / `tool_calls` API, including well-formed JSON arguments. Parallel tool calls are used when the model offers them.
- **JSON-schema structured output** (`response_format: {type: "json_schema"}`) for the planner and the Sentinel. For a server that ignores `response_format`, set `structured_output = "tool_call"`: the answer is then the arguments of one forced `respond` tool call (`tool_choice: "required"`; Anthropic refuses forced tool use, so there it is unforced). Either way Jig checks each answer against its schema. An invalid answer, one that was cut off, or one the server could not read as the tool call is sent back once saying exactly what was wrong, which is logged (without the answer) and recorded with the plan or the Sentinel's verdict. If the second answer is not valid either, the planner fails the goal with both reasons, and the Sentinel fails closed: the action does not go ahead.
  **llama.cpp and `structured_output = "tool_call"`.** llama.cpp's server is affected by [issue #27217](https://github.com/ggml-org/llama.cpp/issues/27217): with `tool_choice: "required"` it holds back the end of the reply until a tool call appears, so a model that has already answered in text can only go on repeating itself, up to the whole context window (one captured run wrote the same 14-character answer 9,306 times over 877 seconds). Jig does not switch modes behind your back. Its own progress check (below) stops such a reply after about 1,000 characters of repetition and asks again, but each one still costs that much generation. With llama.cpp, prefer the default `json_schema` mode unless you need `tool_call`.
- **A context of 32K tokens or more** (advisable). Jig logs a warning at start-up if the server reports less.
- **Decent instruction following**, so it can plan, use tools sensibly and give honest answers when a tool is refused.

Reasoning text (`reasoning_content` or `reasoning`) is optional. When a server sends it, Jig records it in the run steps. When it doesn't, nothing breaks.

**Prompt cache.** The system prompt is the same, byte for byte, for every turn, and a conversation reaches the model only ever growing at its end: what changes from one message to the next (the time, the memories chosen for it, the step budget) is added after the newest message and kept there for later turns. A server with a prompt cache (llama.cpp's server keeps one) then reuses everything it has already read, instead of reading the whole conversation again after a memory is saved or the clock moves on. `scripts/measure_prompt_cache.py` measures this against a real server.

**Output length.** Jig sends no output limit unless you set `max_tokens` under `[model]` (or `[sentinel]`). Local servers (llama-server, LM Studio, Ollama, vLLM) are bounded by their context window anyway. With a reasoning model the thinking counts towards any limit, so a cap would cut off long reasoning before it reaches an answer. The only exception is Anthropic, whose API requires a limit: `profiles/anthropic.toml` sets the documented maximum for its model, and Jig refuses to start with `provider = "anthropic"` and no `max_tokens`. If a reply is cut off, the error says why: either the limit you set, or the conversation filling the context window (with the token counts the server reported). If the server refuses a conversation that no longer fits its context, Jig says that too, rather than passing on a bare HTTP error.

**Long pages.** `web_fetch` returns at most `[tools.web_fetch] max_chars` characters of a page at a time (12,000 by default), so one page can't flood the context. When a page is longer, the result gives its total length and a note with the `next_offset` to read on from. The model can also use `find` to get only the passages that mention a word, each with its offset. Nothing is cut silently. Files work the same way (`read_file`, 20,000 characters at a time), and so do files read from Google Drive, OneDrive and GitHub, and a long email in a Gmail thread. Discord channels are read newest first, with `next_before` to read older messages. Long output from code in the container sandbox is shown as its start and its end, and the whole of it is saved in the workspace under `.jig-output/` to read with `read_file`. An earlier task's result handed to a later task in a goal is cut at 4,000 characters in the prompt, which says so, and the later task reads the rest with `task_result_read`.

**Long conversations.** A conversation is replayed to the model in full while it takes up to 60% of the model's context window. Past that, its oldest turns are left out, in steps of a quarter of the window, and the prompt says how many messages were left out and that the model must not guess what they said. The conversation itself is kept whole, and you can still read all of it. The `start` and `done` chat events give `history_trimmed` when this happens.

**Replies that stop making progress.** Every model call streams, the planner's, the Sentinel's and a task's checked finish included, and Jig watches what arrives itself (`jig/progress.py`), so this works the same with llama.cpp, Ollama, LM Studio, vLLM and cloud APIs and relies on no server setting. Every 256 characters of the reasoning, the reply and each tool call's arguments (each on its own), it checks whether the last 1,000 characters compress to under a tenth of their size, or end in one block repeated exactly three times or more over 1,000 characters. For a structured answer it also stops a complete JSON answer that is followed by more. Code, Markdown tables, CSV, JSON lists and tool-call arguments are repetitive by nature, so there only an exact repeat over 4,000 characters counts, and a message that asks for something "50 times" may get it. When the check fires, Jig closes the stream, which cancels the request at the server, and records only where and why (the kind, the length of the repeat, the ratio and the token position), never the text. A structured answer is then asked for again, afresh: the correction says it repeated itself without quoting it, and the request has a new `seed` and a temperature 0.2 higher (only where the provider takes those fields; anything it refuses is left out and recorded). If the second answer fails as well, the planner fails the goal with both reasons and the Sentinel fails closed. A chat reply or a task is never retried or dropped silently: Jig says "Jig stopped this reply because it was repeating itself", keeps what was written, and offers **Continue anyway** (it carries on from where it stopped, with only a very long exact repeat stopping it) and **Try again**. `scripts/measure_progress_check.py` replays real captured runs and real documents through the check and reports its margins.

**Reasoning that goes round in circles.** A model can also get stuck without repeating itself exactly: it rewrites the same few paragraphs of reasoning with small changes, for tens of thousands of tokens, which no exact repeat and no 1,000-character window sees. So in the reasoning (only there), Jig also counts phrases of five words: when, at four looks in a row 150 words apart, at least 80% of the phrases in the last 600 words are ones the model has now written four times or more, the reply is stopped as going round in circles. There is no length limit in this: reasoning that is getting somewhere keeps bringing new phrases however long it runs, and a plan or a file drafted again, even three times over, writes its phrases only two or three times. What follows is the same as for a repeat, with its own words: a chat reply or a task says "Jig stopped this reply because it was going round in circles" and offers **Continue anyway** (which this check then leaves alone) and **Try again**; a structured answer is asked for once more, afresh, with a nudge ("you were going round in circles in your thinking; give your best answer now"), and the Sentinel fails closed if that fails too. Only the share of reused phrases and the position are recorded, never the text. Measured by the same script on real data (a real circling run, real reasoning from Jig's planner, Sentinel and chats, LongMemEval's replies, Jig's documents, and real code and plans redrafted), the circling run holds about 95% and genuine text at most about 40%; there is so far only one real circling run to tune on.

**No time limit, but a stuck server is reported.** There is no limit on how long a reply may take or how many tokens it uses. Jig only gives up on a server that has gone silent: nothing for `first_token_timeout_s` (600 seconds) after asking, which allows for reading a long prompt or waiting for a busy server, or for `liveness_timeout_s` (120 seconds) in the middle of a reply. The error says which, and how long it waited. Each streamed request asks for its token counts with the standard `stream_options: {"include_usage": true}`, which the step budget's "context used" relies on; a server that refuses it is asked again without it, and its calls are recorded as having no counts (`usage_missing`) rather than given made-up ones. While Jig is replying, its corner shows a **Stop the reply** button, always in the same place, on a narrow window too; what had arrived is kept in the conversation.

**The same action again and again.** If a run makes the same tool call with the same arguments and gets the same result three times, Jig stops it and says so plainly. This complements the step limit.

**Step limit.** One request may use up to `[runtime] max_steps` model calls (12 by default). After each step's tool results, Jig adds a short line saying how many are left and how much of the context window is used, so the model can plan to finish in time. If it still reaches the limit, Jig makes one more call with no tools offered, so nothing more can be done, and you get the model's own account of what it did and what is left instead of a bare error: in chat the `done` event has `limit_reached`, and you can ask it to carry on; a task fails with that account as its result.

**How a task ended.** After a background task replies, Jig asks the model for one more, checked, structured answer: whether the task was `done`, `partial` or `could_not` be done, a one- or two-sentence summary, and what the result rests on (the tools, pages or files used). It is the task's `outcome` in the API (`GET /tasks/{id}`). A task that says it `could_not` be done fails, so tasks that depend on it don't run on nothing; tasks that do run are told how the earlier ones ended. Chat replies stay free text.

At start-up Jig runs a **real capability check** against the configured model. It requests a specific tool call and checks the name and arguments, and it requests a JSON-schema answer and checks the value. If either check fails, the agent stays off (Jig serves only its set-up page, in set-up mode) and Jig explains why. Run the same checks any time with `jig health`.

Jig was developed on **Ternary Bonsai 2 27B** (PrismML fork), and this release was tested (the full test suite, demos, vision and attachments) with **Qwen 3.8 27B** (Q6_K with MTP heads) on a llama.cpp fork with its vision projector (mmproj). That is one example setup, not a requirement.

### Vision (optional)

Vision is optional and works with any vision-capable model. Images are sent as standard OpenAI `image_url` content parts (base64 data URLs). Turn it on with:

```toml
[vision]
enabled = true
```

With vision enabled, the start-up check (and `jig health`) also sends a **real test image**: a square in a randomly chosen colour. The model has to name the colour, or the agent stays off. Tools that need vision, such as `browser_screenshot` with a question, raise `VisionUnavailable` with a clear message if vision is off; they never quietly carry on without the image. Without vision, use `browser_read` for the page text.

In chat you can attach a PNG or JPEG. Jig sends it as image input when vision is on. If vision is off, or this model cannot see images, Jig says so and does not answer as if it had seen the picture. Word (`.docx`), PDF, plain text and Markdown can be attached either way: Jig reads the text, labels it untrusted (instructions inside the file are not followed), and gives a long document one part at a time. A Word document includes headers, footers, comments, text boxes, footnotes and endnotes, and pictures in the document. A PDF is read page by page, and a picture on a text page is sent too. A scanned page, which has no text layer, is sent as a picture of the page when vision is on. Jig does not run OCR. A password-protected PDF is refused. At most 4 of these document pictures are sent at a time, and Jig says when only the first were sent. Pictures smaller than 64 pixels on both sides are left out. EMF and WMF pictures are not sent. The files are kept with that conversation. See [Attaching files](docs/getting-started.md#attaching-files).

How to serve a vision model:

- **llama.cpp** (and forks): download the model's vision projector (`mmproj-*.gguf`) and add `--mmproj <file>` to `llama-server`. The release test used Qwen 3.8 27B (Q6_K with MTP heads). Its projector file is `mmproj-Qwen3.8-27B-F16.gguf`:
  `llama-server -m <model>.gguf --mmproj mmproj-Qwen3.8-27B-F16.gguf --jinja ...`
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
 │  Tools: web_fetch · read_file · list_files · write_file · read_attachment · notes · memory · time
 │         (container backend) run_command · run_python · browser_*         │
 │  Sandbox: per-agent folder, or a hardened Docker container ──► egress proxy (lease + core rules)
 │  Vision (optional image input)  Vault (DPAPI / keyring / keyfile)         │
 │  SQLite: goals, tasks, runs, steps, approvals, rules, memory+FTS5, notes, audit (append-only)
 │  Event bus ──► avatar state tracker ──► /events (WebSocket) and /events/sse
 └──────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility |
| --- | --- |
| `jig/config.py` | TOML config and environment overrides; `[model]` and `[sentinel]` endpoints (the Sentinel uses the agent's model unless you set another) |
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

## Running it from the command line

Install from source as in [Get started](#on-mac-linux-or-windows-without-the-installer). For development, install the test tools too: `pip install -e ".[dev]"` (see [CONTRIBUTING.md](CONTRIBUTING.md)).

```powershell
.\.venv\Scripts\jig serve                     # agent, API and web UI on http://127.0.0.1:8766; opens Jig
.\.venv\Scripts\jig ui                        # opens Jig signed in (from another terminal); --browser for the browser
.\.venv\Scripts\jig health                    # checks the endpoints and runs the capability probes
.\.venv\Scripts\jig chat                      # chat in the terminal
.\.venv\Scripts\jig stop                      # turns Jig off
```

`jig serve` uses `jig.toml`, which points at llama.cpp on `http://127.0.0.1:8080/v1`. If your model app is something else, choose it on the set-up page, or start Jig with its profile: `jig --config profiles/lmstudio.toml serve` (LM Studio) or `jig --config profiles/ollama.toml serve` (Ollama). `jig ui`, `jig chat` and `jig stop` find the running Jig through its data folder, on whatever port it uses. If the model isn't set up or fails its checks, `jig serve` starts in **set-up mode**: the web UI runs, while the agent, scheduler, tools and connectors stay off until a model passes the checks. What you choose in the web UI is saved in the data folder's `settings.toml`, which overrides `[model]`, `[sentinel]`, `[vision]`, `[sandbox]` and the timezone in `[runtime]` in `jig.toml`; `jig.toml` itself is never rewritten. Delete `settings.toml` to go back to `jig.toml` alone.

**Running code needs Docker.** Out of the box (`[sandbox] backend = "directory"`), Jig chats, reads the web, remembers, runs schedules and works with files in its own folder, but it **cannot run code, shell commands or a web browser**. Those run only inside an isolated Docker container. To turn them on, install and start Docker Desktop, then use **Settings > Model and connection > To let Jig run code**: it builds Jig's container (a one-off download) and turns running code on after checking it works. From the terminal: `jig sandbox build` once, set `[sandbox] backend = "container"` and restart Jig (details in [Container sandbox and headless browser](#container-sandbox-and-headless-browser)); `jig sandbox status` tells you what is missing. When code is off, the agent is told so and says so plainly rather than pretending.

### Web UI

`jig serve` also serves a dependency-free web UI at `http://127.0.0.1:8766/`. It has the live avatar (on the real `/events` stream), streaming chat with collapsed thinking, activity (goals, tasks and runs, with create, cancel, pause, resume and delete), the approvals inbox with the Sentinel's verdict and reason, memory and notes (list, search, view, edit and delete one, delete all notes, or forget everything), conversations and finished jobs (read and delete one, or delete them all), schedules (list, add, pause, resume and delete, with the next run and the last result), the custom rules editor (core rules are read-only), the audit log, the model status and whether Jig can run code. The avatar is served from `avatar/jig-avatar.js` in this repository, not copied, so the UI needs an editable install (`pip install -e .`), a source checkout, or the Windows installer (which bundles it).

Run `jig ui` to open it signed in (see [Access and the API token](#access-and-the-api-token)): in its own window on Windows (see [Jig's window](#jigs-window-on-windows)), in your browser elsewhere or with `jig ui --browser`. `jig ui --print-url` prints the one-time link instead of opening anything.

### Choosing a model server

`jig.toml` points at `http://127.0.0.1:8080/v1` and discovers the model from `/v1/models`. Discovery fails loudly if the server offers more than one model; in that case, set `name`. The example profiles are in `profiles/`:

| Profile | Server |
| --- | --- |
| `profiles/llamacpp-bonsai.toml` | llama.cpp / forks (`llama-server --jinja --parallel 4`); the development example |
| `profiles/ollama.toml` | Ollama (`http://127.0.0.1:11434/v1`); set a tool-capable model and a context of 32K or more |
| `profiles/lmstudio.toml` | LM Studio local server (`http://127.0.0.1:1234/v1`) |
| `profiles/vllm.toml` | vLLM with `--enable-auto-tool-choice --tool-call-parser ...` |
| `profiles/openai.toml`, `openrouter.toml`, `anthropic.toml`, `gemini.toml` | Cloud models; see [Using a cloud model](#using-a-cloud-model) |

Use one with `jig --config profiles/ollama.toml serve`, or set `JIG_CONFIG`. `[model.sampling]` is sent exactly as written, so include only parameters your server accepts. If your server needs an API key, store it in the vault with `jig model key set <name>` and set `api_key_secret = "model-key.<name>"`, or put it in an environment variable and name that variable in `api_key_env`.

Jig works out where each endpoint is from its address alone (it never looks the name up): loopback, private network addresses (10/8, 172.16/12, 192.168/16, fc00::/7), link-local, Tailscale (100.64.0.0/10, `*.ts.net`) and local names (`localhost`, single-label names, `.local`, `.lan`, `.home.arpa`, `.internal`) are **local**; everything else is **cloud**. `jig health`, `jig model cloud status`, `GET /status` and Settings in the web UI show where the agent and the Sentinel run.

### The safety checker's model

By default the safety checker (the Sentinel) is the agent's own model on the same server. `[sentinel]` inherits every `[model]` setting, so no second model is loaded and Jig needs no more GPU memory than the agent alone. The Sentinel is still isolated: each review is a separate request with its own system prompt and no tools, and it never sees the agent's conversation. At start-up Jig runs the capability checks once for the shared model, and Settings shows "same model as the agent".

**Optional, advanced: a different model.** You can point the safety checker at another model or server:

```toml
[sentinel]
base_url = "http://127.0.0.1:11434/v1"
name = "your-reviewer-model"
```

That model must also pass the start-up checks (a JSON-schema answer). It costs its own memory on top of the agent's: its weights, plus its context cache, plus the server's working buffers. On one GPU, both models have to fit together, or the server has to swap between them on every review. Use a different model only if you have the memory to spare, or if you want reviews from a different model family or kept on a local server while the agent is in the cloud.

### Lower-memory GPUs (8–12 GB)

Jig does not need a 32 GB card. One model serves as both agent and safety checker, so what matters is that one model and its context fit. As a measured example, granite 4.2 8B at Q4_K_M (a 5.3 GB file) on the upstream `llama-server` (`-ngl 99 --jinja --parallel 1`) passes Jig's tool-calling and JSON-schema checks. It took about 8.1 GB of GPU memory with a 16K-token context, and about 10.6 GB with 32K. The server reported 4.9 GB of weights, a 2.5 GB or 5.1 GB context cache and about 0.2 GB of working buffers. Those were single measurements on an RTX 5090, read as the change in total GPU memory, so allow some margin.

- **12 GB:** an 8B model at Q4_K_M with a 16K context fits with room to spare. 32K fits, but only just.
- **8 GB:** we have not yet measured a setup that fits. A smaller model, a lower-bit quantisation or a shorter context reduces memory. llama.cpp can also store the context cache at lower precision (`--cache-type-k` / `--cache-type-v`).
- **Context:** Jig warns at start-up if the context is under `[runtime] min_context_tokens` (32,768), because long tasks and memory lookups need room. It still runs with less.
- **The guard:** whatever you choose, Jig's agent won't start if the model fails the tool-calling or JSON-schema check (or the vision check, with `[vision] enabled = true`). It never falls back to another model. A model that is too small to call tools reliably is caught there, not halfway through a task.

### Tests

The tests use the real configured model server and a real temporary SQLite database. Nothing is mocked. Some tests also fetch `https://example.com` and `https://httpbin.org`, so they need internet access. The container and browser tests (`tests/test_container_sandbox.py`, `tests/test_browser.py`) need Docker running and the sandbox image built (`jig sandbox build`). The vision tests need `[vision] enabled = true` and a vision-capable model. `tests/test_vault_keyfile.py` needs no model. `tests/test_compose_stack.py` runs against a live compose stack: start it, then set `JIG_STACK_URL` (for example `http://127.0.0.1:8766`) and run `pytest -m compose`. Without `JIG_STACK_URL` it is skipped. `tests/test_cloud_models.py` tests the cloud path without a cloud account, against a real llama-server over TLS reached through a public host name (`llama.localtest.me`). `tests/test_cloud_live.py` runs against each real provider only when you set `JIG_LIVE_OPENAI_KEY`, `JIG_LIVE_OPENROUTER_KEY`, `JIG_LIVE_ANTHROPIC_KEY` or `JIG_LIVE_GEMINI_KEY` (and optionally `JIG_LIVE_<PROVIDER>_MODEL`); otherwise each is skipped with the reason.

```powershell
.\.venv\Scripts\python -m pytest -q
```

### Demos

With `jig serve` running:

```powershell
.\.venv\Scripts\python scripts\demo.py --url http://127.0.0.1:8766       # goal -> plan -> approval -> done
.\.venv\Scripts\python scripts\chat_demo.py --url http://127.0.0.1:8766  # streaming chat with avatar states
```

## Using a cloud model

Jig is built for local models, and a local model is the private choice. If you would rather use a cloud model, Jig supports OpenAI, OpenRouter, Anthropic and Google Gemini through their OpenAI-compatible APIs. You need an **API key** from the provider, and the provider **charges you** for what you use.

The easy way is in the web page: on the set-up page (or **Settings > Model and connection > Change model**), choose **Use a cloud model instead**, pick the provider, paste the key, read exactly what will be sent to them, and agree. Jig checks the key and the model for real before switching. The safeguards:

- **The privacy trade-off is real.** With a cloud agent, your conversation, the memories Jig adds to your messages and tasks (see [Memory and notes](#safety-model)), any memory or note the agent looks up, the tool results it works with, and any images you share are sent to the provider, under its terms and retention policy. If the Sentinel is also on the cloud, every action Jig wants to take, with its details, is sent too. Your memories and notes, history, audit log, rules and vault are stored only on your machine; only what goes into a request leaves it, and deleting a memory or note in Jig does not delete what the provider has already received.
- **Explicit consent, twice.** Jig's agent stays off with a cloud endpoint until that section of the config has `allow_cloud = true` (the cloud profiles, which the web page uses, have it) **and** you have confirmed, on the set-up page, in Settings or with `jig model cloud confirm`. Each shows exactly what is sent and records your confirmation in the audit log. The config line is per role: the safety checker never inherits it, so `[sentinel] allow_cloud = true` is needed too. When the safety checker uses the agent's endpoint (the default), one `jig model cloud confirm` covers both roles. It is recorded once, naming both. A different endpoint asks again. `jig model cloud revoke` withdraws it.
- **HTTPS only.** A cloud endpoint over `http://` is a configuration error.
- **Keys in the vault.** `jig model key set <provider>` stores the key in the vault (prompted without echo, or `--stdin`). It is never logged, never shown to the model, never available to any tool (a core rule blocks it and tool results are redacted), and it is redacted from errors and the audit log, including the masked form some providers echo back. `jig model key status` shows which keys are stored, and `jig model key delete <provider>` removes one. `api_key_env` still works if you prefer an environment variable.
- **The safety checker uses the same model by default.** In the cloud profiles the safety checker is the agent's cloud model, so you need no local GPU. Its reviews go to the provider too. Each profile has a commented block for running the safety checker on a local model instead. Then its reviews never leave your machine and it is independent of the agent's provider, but you need a local model server and the memory it takes. When it points somewhere else, the safety checker gets none of `[model]`'s key or provider settings.
- **You can see it.** Settings > Model and connection shows "Local" or "Cloud: host" for the agent and the safety checker, with a short note on what is sent, and a "Cloud model" label sits next to the health dot whenever the agent is on the cloud.

From the terminal instead, set up a provider in three steps (Anthropic shown; use `openai`, `openrouter` or `gemini` in the same way):

```powershell
.\.venv\Scripts\jig --config profiles/anthropic.toml model key set anthropic   # paste the key
.\.venv\Scripts\jig --config profiles/anthropic.toml model cloud confirm       # read what is sent, then confirm
.\.venv\Scripts\jig --config profiles/anthropic.toml serve
```

| Profile | Provider and endpoint | What Jig handles differently |
| --- | --- | --- |
| `profiles/openai.toml` | [OpenAI](https://developers.openai.com/api/docs/guides/function-calling), `https://api.openai.com/v1` | No output limit is sent by default. If you set `max_tokens`, it is sent as `max_completion_tokens` (`max_tokens` is deprecated and not accepted by reasoning models). Chat Completions has no function calling for some newer models; the start-up check fails clearly if yours is one. Key: [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `profiles/openrouter.toml` | [OpenRouter](https://openrouter.ai/docs/guides/features/tool-calling), `https://openrouter.ai/api/v1` | Routes only to providers that support every parameter sent (`require_parameters`) and that don't collect data (`data_collection = "deny"`). Reasoning details are passed back unchanged. Key: [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) |
| `profiles/anthropic.toml` | [Anthropic](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk), `https://api.anthropic.com/v1` | Its API requires an output limit, so the profile sets `max_tokens = 128000`, the documented maximum for `claude-sonnet-5-5`. If you choose another model, set its documented maximum; Jig won't start without one. Its compatibility layer ignores `response_format`, so structured output is asked for as a tool call and checked. Temperature is capped at 1, and sampling keys it would silently ignore are refused. Key: [platform.claude.com/settings/keys](https://platform.claude.com/settings/keys) |
| `profiles/gemini.toml` | [Google Gemini](https://ai.google.dev/gemini-api/docs/openai), `https://generativelanguage.googleapis.com/v1beta/openai` | Thought signatures on tool calls are returned with the conversation, as Gemini requires. Sampling keys it would silently ignore are refused. Key: [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) |

Each profile cites the documentation it was checked against. Anything a provider doesn't support is a configuration error rather than a silent guess, and the same real capability check runs at start-up as for a local model. `[model.launch]` can't be used with a cloud endpoint.

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

Jig can start by itself when you log in, but **only if you turn this on**. It is never enabled by default or by the agent. The Windows installer offers it as an unticked box that says what it adds, and then Start with Windows starts Jig's tray icon, which starts Jig. You can also turn it on in Settings > Starting with Windows. `jig autostart enable` first shows exactly what it will register: the command line, the trigger, the account it runs as, the log location and the settings. It registers nothing until you answer `y` (or pass `--yes` in a script). Turning it on or off is recorded in the audit log (`autostart.enabled`, `autostart.disabled`).

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

## Schedules

A schedule is a job Jig does by itself at set times, in the background: "every weekday at 08:00, summarise the technology headlines". Each run is an ordinary task, so the usual rules, the Sentinel and approvals apply, and research schedules (the default) can only read and take notes. Manage them in Settings > Schedules, which shows each schedule's next run and the outcome of its last one, or through `/schedules`.

A schedule repeats in one of these ways. Calendar times are wall-clock times in the schedule's own timezone (Jig's timezone unless one is given; see [Timezone](#timezone)), so 08:00 stays 08:00 when the clocks change. A time that a spring change skips runs an hour later that day, and a time that an autumn change repeats runs once.

| `repeat` | Example |
| --- | --- |
| `{"kind": "daily", "at": "08:00"}` | every day at 08:00 |
| `{"kind": "weekdays", "at": "08:00"}` | Monday to Friday at 08:00 |
| `{"kind": "weekly", "days": ["mon", "thu"], "at": "18:30"}` | Mondays and Thursdays at 18:30 |
| `{"kind": "interval", "interval_s": 3600}` | every hour, counted from the last run (the original `interval_s` field still works) |
| `{"kind": "cron", "cron": "*/15 9-17 * * mon-fri"}` | a five-field cron expression: lists, ranges, steps, month and day names, `@daily` and the like |

```powershell
$h = @{ Authorization = "Bearer $(.\.venv\Scripts\jig token show)" }
$body = @{ name = "Tech headlines"; prompt = "Summarise the BBC technology headlines"
           repeat = @{ kind = "weekdays"; at = "08:00" }; timezone = "Europe/London" } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8766/schedules -Headers $h -ContentType application/json -Body $body
```

**Asking Jig.** Say "every weekday at 8am, summarise X" in the chat and the agent proposes it with the `schedule_create` tool. That tool is human-only: a core rule means it always asks you first, with a card that shows the schedule's name, what it will do, how it repeats and its next three runs. Nothing is saved unless you say yes. The agent can also list the schedules (`schedule_list`), but only you can change or delete them.

Paused schedules don't run. When you resume a calendar schedule that missed its time while paused, it waits for its next time; an overdue interval schedule runs straight away. While the whole agent is paused, no schedule runs. After sleep or a clock change, each overdue schedule runs once (see [Running Jig always-on](#running-jig-always-on)).

## Container sandbox and headless browser

By default (`[sandbox] backend = "directory"`), the file tools are confined to a folder and Jig cannot run code or use a browser. To run code, shell commands and a headless Chromium in an isolated Linux container, use the container backend:

```powershell
.\.venv\Scripts\jig sandbox status     # is Docker running, is the image built, what is left to do
.\.venv\Scripts\jig sandbox build      # docker build -t jig-sandbox:0.1.0 jig/sandbox_container/image
```

**Why it isn't the default.** The container backend refuses to start without a running Docker and the built image, and Jig never falls back to the directory sandbox. As the default, it would keep Jig's agent off on a machine without Docker, or whenever Docker Desktop is not running yet at sign-in. So the directory backend stays the default and Jig says plainly what is missing: `jig sandbox status`, `GET /sandbox` and Settings > Model and connection > To let Jig run code all list the steps.

```toml
[sandbox]
backend = "container"     # or set JIG_SANDBOX_BACKEND=container
image = "jig-sandbox:0.1.0"
cpus = 2.0
memory = "2g"
pids_limit = 512
egress_ports = [80, 443]
```

If the container backend is selected and Docker is not running, or the image has not been built, Jig's agent stays off (set-up mode) and Jig says why. It never falls back to the directory sandbox. The image is based on `mcr.microsoft.com/playwright/python` (Chromium included) and adds a pinned `playwright` package, an unprivileged `jig` user (uid 10001) and two small scripts: the browser server and the egress relay.

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

## Search (optional)

Jig can look things up on the web through [SearXNG](https://docs.searxng.org/), a separate program. SearXNG is **not part of Jig** and is not in the installer. It is free software under the GNU Affero General Public License (AGPL-3.0-or-later); Jig itself is Apache-2.0. Jig downloads a pinned upstream release only when you ask it to, into its own folder and its own Python environment, never into this repository.

In **Settings > Search**, **Install search** downloads that release. Jig starts SearXNG when a search needs it and stops it when Jig stops. It listens on this computer only (`127.0.0.1`, port 8090, or another free local port if 8090 is taken) and does not start when you sign in to Windows. If SearXNG is already running on this computer, Jig uses that one and does not install a second copy or stop it. **Remove search** deletes only the copy Jig installed.

Nothing leaves this machine except the searches SearXNG itself sends to the search engines it uses. Search reads SearXNG's JSON results. If SearXNG is not installed or will not start, Jig says so. It does not scrape DuckDuckGo or any other engine, and it does not invent results.

The same actions are on the command line: `jig search status`, `jig search install`, `jig search use on` or `off`, and `jig search remove`. Step by step: [docs/search.md](docs/search.md). The other guides are in [docs/README.md](docs/README.md).

## Connectors

Connectors let Jig work with your own accounts. Microsoft and GitHub sign in with Jig's own public apps (no setup, no secret; an organisation can use its own app instead); Google needs your own small Google app for now; Discord and WhatsApp use your own bot or Meta Cloud API token. Each talks straight from your computer to the provider, and keeps its tokens only in the vault. Everything can be connected from Settings > Connections, with plain step-by-step instructions. Setup for each provider, with the scopes and why, is in [docs/connectors-setup.md](docs/connectors-setup.md).

Every connector is built and tested against the provider's real service without an account (a made-up token or client gets the provider's real refusal; the gate, limits and approvals run for real). Each also has an opt-in live end-to-end test (`tests/test_connector_<name>_live.py`) that needs your own account's setup and is skipped, with the reason, until then.

| Connector (`jig connect` name) | Sign-in | Tools |
| --- | --- | --- |
| Gmail (`gmail`) | Google OAuth, your own Google app | `gmail_search`, `gmail_read_thread`, `gmail_list_labels` (read); `gmail_create_draft`, `gmail_send`, `gmail_send_draft`, `gmail_reply`, `gmail_modify_labels`, `gmail_archive` (actions) |
| Google Calendar (`google-calendar`) | Google OAuth | `gcal_list_calendars`, `gcal_list_events`, `gcal_get_event` (read); `gcal_create_event`, `gcal_update_event`, `gcal_cancel_event` (human-only) |
| Google Drive (`google-drive`) | Google OAuth | `gdrive_search`, `gdrive_read_file` (read); `gdrive_create_file`, `gdrive_update_file` (actions; only files Jig created) |
| Outlook calendar and OneDrive (`microsoft`) | Microsoft OAuth with Jig's app (personal, work or school), one sign-in | `outlook_list_calendars`, `outlook_list_events`, `outlook_get_event`, `onedrive_search`, `onedrive_list_folder`, `onedrive_read_file` (read); `outlook_create_event`, `outlook_update_event`, `outlook_cancel_event` (human-only), `onedrive_upload_file` (action) |
| GitHub (`github`) | the Jig GitHub App (device code), or a fine-grained token | `github_list_repos`, `github_list_issues`, `github_read_issue`, `github_read_file` (read); `github_comment`, `github_create_issue` (human-only) |
| Discord (`discord`) | bot token | `discord_list_channels`, `discord_read_channel` (read); `discord_post_message` (human-only) |
| WhatsApp (`whatsapp`) | Meta WhatsApp Business Cloud API token, phone number ID and WhatsApp Business Account ID | `whatsapp_account` (read); `whatsapp_send_message` (human-only). Incoming messages are webhook-only, so Jig does not read them |

```powershell
.\.venv\Scripts\jig connect gmail --client-json <downloaded Desktop app client JSON>   # opens Google's consent page
.\.venv\Scripts\jig connect microsoft --access write                                  # opens Microsoft's sign-in page
.\.venv\Scripts\jig connect github --access write                                     # shows a code to type at github.com/login/device
.\.venv\Scripts\jig connect discord                                                   # asks for the token without echoing it
.\.venv\Scripts\jig connections                                                       # status, account, scopes
.\.venv\Scripts\jig disconnect gmail                                                  # revokes where the provider can, deletes the tokens
```

Settings > Connections in the web UI shows the same, with each provider's steps and links, and connects every connector: sign-in links, GitHub's device code, token and password forms, and Google's client file. It only accepts these from the computer Jig runs on; typed values go straight to the vault and are never echoed back, logged or put in an error. The API is `GET /connections`, `POST /connections/{provider}/connect` `{confirm: true, access, method, values}` (host only; returns the sign-in link or device code, or connects a token), `POST /connections/{provider}/disconnect` `{confirm: true}`, and `POST /connections/{google|microsoft}/client` `{confirm: true, ...}` and `DELETE /connections/{google|microsoft}/client` (host only) for your own app's client.

**How connectors are kept safe.**

- **Sign-in:** OAuth 2.0 with PKCE and a one-shot loopback listener on `127.0.0.1` that checks the `state` value (Google and Microsoft; Microsoft as a public client with no secret). GitHub uses the GitHub App device flow: no secret, expiring user tokens renewed with a refresh token, and only the repositories you chose when installing the app. Each asks only for the scopes of the access level you choose (`--access`). Token connectors take the token at a hidden prompt, on standard input or in Settings, and check it with the provider before storing anything. If Jig's own Microsoft or GitHub app isn't configured, connecting says so; it never quietly switches to another app.
- **Tokens:** access and refresh tokens and the app client are in the vault as `connector.<provider>.*`. The model never sees them; a core rule stops any tool from naming them with `{{secret:...}}`; the generic `/vault` API refuses to write or delete them; and every token value used is redacted from tool results, errors and the audit log. A connector sends its token only to its provider's own API hosts.
- **Reads and actions:** reading is a `read` tool, so it works in read-only mode and is not reviewed. Every draft, send, post, comment, calendar change, file save and label change is an outbound side effect: the Sentinel reviews it, and it needs your approval. Sending mail and messages, posting, commenting and changing a calendar are human-only, so no rule can make them automatic. Drafts, label changes and file saves ask by default. Read-only mode refuses all of them. There are no tools to delete mail or files, share files, or close, merge or push on GitHub; adding `TRASH` or `SPAM` is refused; posts never ping a whole channel.
- **What it refers to:** before review, Jig looks up what an action refers to (the thread, the calendar and event, the folder or file, the repository and issue, the channel or room) and shows it on the approval card and to the Sentinel, marked as text written by other people.
- **Untrusted content:** the agent is told that content from connected accounts is information, never instructions, and connector results say so too.
- **Limits for testing:** `[connectors.<name>] allowed_targets` (the calendars, folders, repositories, channels or phone numbers Jig may change), `allowed_recipients` and `required_prefix` (for example `"[Jig test]"`) are checked by the gate before the Sentinel or an approval, and again just before acting.
- **Errors:** an expired or revoked grant marks the connection "needs reconnecting" and says how to fix it. When a provider says "slow down", Jig waits as asked (at most 3 times and 30 seconds in all) and then fails with the reason. A failed send is never retried.

### MCP servers

MCP is how you add tools Jig does not ship. In Settings > MCP servers you name a program on this computer and, if it needs them, its arguments, one per line. Jig starts that program itself. It does not use a shell, and it does not connect to an MCP server over the internet: a host chosen by the model, a tool or the server would sit outside the allow-list every other outbound call uses. The same steps are numbered on that page. The walk-through, including the official filesystem server, is in [docs/mcp.md](docs/mcp.md).

A token goes in the vault, as an environment variable for that server, and is given to the program when it starts. It is never put in the chat, the logs or the audit log, and no tool can name it with `{{secret:...}}`. The generic `/vault` API will not write or delete it. Refresh tools starts the program again, so a new secret takes effect.

Jig lists the server's tools and can call them through the same gate as its own tools. A third-party tool must declare its effect (`read`, `private_write` or `side_effect`), whether it is outbound, and its category. Put that in `_meta.jig`, or in `annotations`. MCP's own hints count too: `readOnlyHint` and `destructiveHint` for the effect, and `openWorldHint` for outbound.

- A declared read can run when you have allowed reading. If it does not say it stays on this computer, the safety checker still reviews it.
- A tool that declares no effect fails closed. Jig treats it as able to send, change or delete, the safety checker reviews it, and it always needs your approval. A rule that says allow cannot skip that.
- Read only, on the server, hides tools that are not reads and refuses them if they are called. Read and act can use them, still through the gate.

The audit log records that a server was added, how many tools it listed and their names. It does not record the program, the arguments, a tool's description or a secret.

**Paying and booking.** Jig always asks you first and never types card details. Before every browser click and form submission, Jig inspects the page without changing it for a checkout, payment or booking (card or bank fields, a payment provider's frame, a "Pay", "Place order" or "Book" button, or a checkout page with prices). If it finds one, the core rule `payment-checkpoint` asks you, whatever your rules say, and the approval card shows the site, the items and the total found on the page; the avatar shows that Jig is shopping. The browser itself refuses to type into card, security code, expiry, IBAN, sort code or account number fields, so Jig never enters payment details. This needs the container backend's browser (and so Docker). Its tests run on real public demo pages and stop at the confirmation step.

## Safety model

Jig enforces safety at the tool level, in code. The prompt describes the rules but cannot loosen them. Every tool call passes through one gate, in this order:

1. **Schema.** The tool must exist and the arguments must match its JSON schema. Every problem with them, nested ones included (an address in a list that isn't text, say), is reported to the model at once, so it can fix them all in one go.
2. **Mode.** Every tool is tagged `read`, `private_write` or `side_effect`. In **research mode** (proactive background work, and the web UI's "Just look, don't touch" setting), only `read` and `private_write` tools are offered to the model, and the gate refuses anything else even if the model tries it. So research mode can read permitted sources and keep its own private notes and memories, but it won't change, send or save anything of yours, or take actions. **Action mode** has every tool, subject to the steps below.
3. **Core rules.** These are hard-coded and cannot be overridden (`GET /rules/core`). No credential or password changes. A secret may be used only by the tools on its allow-list, and model API keys, connected accounts' tokens and MCP server secrets by none. Sending a secret outbound always needs a human. Outbound tools may never reach localhost, the model server, Jig's own API or the local network, which also stops the agent from approving its own requests. Human-only tools (sending messages, posting, changing calendars, and an MCP tool that did not declare an effect) always need approval, and so does any click or submission that looks like a payment, checkout or booking. Core rules can only make a decision stricter.
4. **Custom rules.** Editable `allow`, `ask` or `block` rules per tool, with glob matching on tool names and, optionally, on an argument such as `url` matching `https://shop.*`. They are stored in SQLite and managed through `/rules`.
5. **The Sentinel.** Every outbound or side-effecting action is reviewed by an isolated model call with its own system prompt and no tools. It sees only the trusted intent, the proposed action and the policy findings, never the agent's conversation, so injected web content cannot address it. It returns a structured verdict of `allow`, `ask_user` or `deny`, with a risk level and a reason. A `deny` cannot be overridden. If the Sentinel fails or returns an invalid verdict, the action does not run and the error is reported. An argument too long to show it whole (over 3,000 characters, such as a long file or command) is shown by its start and its end, and the Sentinel is told what was cut. An action it could not see in full is never simply allowed: an `allow` becomes `ask_user`, and the reason says what was not shown.
6. **Approvals.** If a core rule, custom rule or the Sentinel asks for a human, an approval is queued (`/approvals`), the task's status becomes `waiting_approval`, and the run pauses. It resumes as soon as you approve or deny. Runs are checkpointed after every step, so a paused or interrupted task picks up where it left off after a restart.
7. **Vault.** Tools use secrets by reference (`{{secret:NAME}}`). The real value is inserted only at the moment the tool runs, after review, and any occurrence of it in the result is redacted before it reaches the model or the audit log. Values can be written through the API but never read back. The generic `/vault` API refuses to write or delete `connector.*` and `mcp.*` secrets. Jig uses Windows DPAPI on Windows and the `keyring` library elsewhere, and refuses to start if neither is available. In containers, `[vault] backend = "keyfile"` uses a key you supply as a Docker secret (see [Run with Docker](#run-with-docker)).

**Audit.** Every model-call summary, tool call and result, policy decision, Sentinel verdict, approval, vault use, state change, and rule or memory change is appended to an audit log that SQLite triggers make append-only. Query it with `GET /audit?kind=tool&task_id=...`. Because it can't be edited, it never holds what was said: it keeps ids, sizes and decisions. A tool call is recorded with each argument's size (numbers, flags and ids as they are), a result as its size and the ids it touched, a Sentinel verdict as `verdict`, `risk` and the reason's length, an approval as the rules that asked and your answer, and errors, titles and final replies as their type and length. The full details stay with the run, task or conversation, where you can read them and delete them. Configuration you set up yourself is still named: schedule names, rules, secret names, connected accounts, paired devices, and the hosts the code sandbox was allowed or refused. Entries written by Jig versions before this rule are left exactly as they are; `GET /audit/older-with-content` counts those that can quote a conversation or job, and Settings > History says so when there are any.

**Memory and notes.** Memories (what Jig knows about you) and notes (what Jig writes down for itself while it works) are stored only in Jig's SQLite database on your machine, and you can view, edit or delete any of them at any time, in Settings > Memory and notes or through the API:

- memory: `GET /memory`, `POST /memory/search` `{q, limit}` (the words go in the body, never the address), `POST /memory`, `PATCH /memory/{id}` and `DELETE /memory/{id}`;
- notes: `GET /notes`, `GET /notes/{id}`, `PATCH /notes/{id}` `{title, body}`, `DELETE /notes/{id}`, and `POST /notes/wipe` `{"confirm": true}` to delete them all;
- `POST /memory/wipe` `{"confirm": true}` forgets every memory at once, and with `"notes": true` every note too.

The agent is given your memories at the end of each message you send, never in its system prompt. With up to 50 that fit in 6,000 characters it gets them all; with more it gets the newest 50 (`[runtime] memory_prompt = "recent"`, the default) or, with `memory_prompt = "relevant"`, the 8 newest and the 20 that best match the message, and it can look up any other with `memory_search`. In a conversation each message adds only memories that are new or changed since the conversation last showed them, and says which have been forgotten. The agent can change a memory with `memory_update` when you ask it to; like `memory_forget`, that is reviewed by the Sentinel and may need your OK. Jig never rewrites your memories by itself.

**Conversations and jobs.** Your conversations and the jobs Jig has done (goals and tasks, with their results) are stored in the same database, and you can read and delete them one at a time or all at once, in Settings > Conversations and jobs (a finished job also has Delete in What Jig's up to) or through the API:

- conversations: `GET /sessions`, `GET /sessions/{id}/transcript` (what you and Jig said), `DELETE /sessions/{id}`, and `POST /sessions/wipe` `{"confirm": true}`;
- jobs: `DELETE /tasks/{id}`, `DELETE /goals/{id}` (the goal, its plan and its tasks), and `POST /jobs/wipe` `{"confirm": true}` for every finished one;
- `POST /forget` `{"confirm": true}`, which is what Settings > Forget everything sends: every memory and note, every conversation and every finished job.

Only finished things can be deleted. A conversation Jig is replying in, or a job that is still going (queued, running, paused or waiting for your OK), has to finish or be stopped first, and the API answers 409 saying so; the bulk deletes and Forget everything leave those and report how many they kept. A task that is part of a goal goes with its goal, and a finished task that an unfinished one still needs is kept. Deleting a conversation or job removes every copy of its text Jig made: the conversation, the runs (every message), the run steps (each tool's arguments and results), the approvals (the details you were shown, the Sentinel's review and your note), the schedule's link to the job, and the recent events kept in memory for pages that reconnect. Files Jig made in its folder are yours, and stay.

Deleting really deletes. SQLite overwrites the freed pages with zeros (`secure_delete`). After forgetting or editing a memory, Jig rewrites the FTS5 index so the old words are gone from it (FTS5 otherwise only marks them deleted), and after every deletion or edit the write-ahead log is checkpointed and truncated, so the old text is no longer in the database files. Deleting all conversations or all jobs, and Forget everything, also rebuild the database file (`VACUUM`), which clears free pages that may still hold text deleted before `secure_delete` was turned on. Notes and conversations have no index, and there are no embeddings. The audit log records each deletion (`memory.forgotten`, `note.deleted`, `conversation.deleted`, `task.deleted`, `goal.deleted`, `everything.forgotten` and so on) with ids and counts, never content. Jig's logs (`jig.log`, `autostart.log`, `tray.log`, `window.log`, and `model-server.log` from a model server Jig starts) never hold what was said either: requests are logged without their query strings, outgoing requests (web searches and pages Jig reads) aren't logged at all, and an error's traceback keeps its type and where it happened but only the length of its message. llama.cpp's server writes no prompts or replies at its normal log level, but does with `-v` or a verbosity above 3, so Jig refuses to start one with those. With a cloud model, what Jig sent the provider is under the provider's terms (see [Using a cloud model](#using-a-cloud-model)).

**Sandbox.** Every file tool is confined to `sandbox/<agent_id>/`. Absolute paths, drive letters, UNC paths, `..`, alternate data streams, reserved device names, and symlinks or junctions that lead out are all rejected. This is a directory jail, not an OS-level sandbox. Code execution and the browser exist only with the container backend (see above), which adds OS-level isolation and gated egress.

## Avatar states

`/events` (WebSocket) and `/events/sse` stream every runtime event, including `avatar.state` events with `{state, variant, background, run_id, task_id}`. `GET /state` returns the current state. The names are the avatar's own (`avatar/README.md`) and live in `jig/constants.py`:

`idle`, `monitoring` (active research schedules, nothing running), `thinking`, `working` with a variant (`browsing`, `writing`, `coding`, `shopping`, `scheduling`), `talking`, `approval`, `paused` (the agent or a task is paused), `success` and `error`. The last two are shown for 3 seconds.

Each tool category maps to a variant: web to `browsing`, files and email to `writing`, time to `scheduling`, and so on. Proactive read-only research is reported as `working` / `browsing` with `background: true`, which the avatar draws dimmed, half-lidded and slower (`setState('working', {task: 'browsing', background: true})`). When several things happen at once, the precedence is approval, then success/error, then foreground talking, working, thinking, then paused, then background working, then monitoring, then idle.

### Pause and resume

`POST /tasks/{id}/pause` pauses a task and `POST /tasks/{id}/resume` puts it back in the queue; it continues from its last checkpoint and reuses any approval already given. A pause takes effect at the next safe point: between steps, during a model call or while waiting for an approval. A tool that is already running is allowed to finish first. `POST /agent/pause` pauses the whole agent (it survives restarts): schedules stop firing, no task starts, and running tasks are interrupted and re-queued. Chat still works. `POST /agent/resume` undoes it, and `GET /agent` reports it.

## API

| Area | Endpoints |
| --- | --- |
| Health | `GET /health` (public, `{"status": "ok"}` only), `GET /status`, `GET /state`, `GET /tools`, `GET /sandbox` (can Jig run code, and if not, what's missing) |
| Auth | `POST /auth/login-code`, `GET/POST /auth/session`, `POST /auth/logout`, `POST /auth/pair` `{code, name}` (public, a pairing code is the credential), `POST /auth/token/rotate` `{confirm: true}` (host only; revokes every device) |
| Agent | `GET /agent`, `POST /agent/pause`, `POST /agent/resume` |
| Events | `WS /events`, `GET /events/sse`, `GET /events/recent?after=` |
| Chat | `POST /chat` `{message, session_id, mode, action, attachment_ids}` (NDJSON stream: `start`, `reasoning`, `content`, `event`, then `done`, `stopped` or `error`; after `stopped`, `action: "continue"` or `"retry"` with the same `session_id`; closing the stream stops the reply), `POST /attachments` (one file: PNG, JPEG, Word, PDF, text or Markdown), `GET /attachments/{session_id}/{id}`, `DELETE /attachments/{session_id}/{id}` (only before it is sent), `GET /sessions?limit=`, `GET /sessions/{id}`, `GET /sessions/{id}/transcript`, `DELETE /sessions/{id}`, `POST /sessions/wipe` `{confirm: true}` |
| Goals | `POST /goals`, `GET /goals`, `GET /goals/{id}`, `POST /goals/{id}/cancel`, `DELETE /goals/{id}` (finished only) |
| Tasks | `POST /tasks`, `GET /tasks?newest_first=&limit=`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`, `POST /tasks/{id}/pause`, `POST /tasks/{id}/resume`, `POST /tasks/{id}/retry` `{continue_anyway}` (a task Jig stopped for repeating itself), `DELETE /tasks/{id}` (finished only), `POST /jobs/wipe` `{confirm: true}`, `GET /runs?task_id=&kind=`, `GET /runs/{id}` |
| Forget everything | `POST /forget` `{confirm: true}` (memories, notes, conversations and finished jobs) |
| Schedules | `POST /schedules` `{name, prompt, mode, repeat \| interval_s, timezone}`, `GET /schedules` (with `next_run_at`, `repeat_text` and `last_task`), `PATCH /schedules/{id}` `{enabled, name, prompt, mode, repeat, timezone}`, `DELETE /schedules/{id}` |
| Approvals | `GET /approvals?status=pending`, `GET /approvals/{id}`, `POST /approvals/{id}` `{approve, note}` |
| Rules | `GET/POST /rules`, `GET/PATCH/DELETE /rules/{id}`, `GET /rules/core` |
| Memory | `GET/POST /memory`, `GET/PATCH/DELETE /memory/{id}`, `POST /memory/wipe` `{confirm: true, notes}` |
| Notes | `GET /notes?limit=`, `GET/PATCH/DELETE /notes/{id}` (`{title, body}`), `POST /notes/wipe` `{confirm: true}` |
| Audit | `GET /audit?kind=&task_id=&run_id=&after_id=&before_id=&newest_first=&limit=`, `GET /audit/older-with-content` |
| Vault | `GET /vault` (names only), `PUT /vault/{name}`, `DELETE /vault/{name}` (not for `connector.*` secrets) |
| Connections | `GET /connections`, `POST /connections/{provider}/connect` `{confirm: true, access}` (host only), `POST /connections/{provider}/disconnect` `{confirm: true}` |
| MCP servers | `GET /mcp/servers`, `POST /mcp/servers` `{confirm: true, label, command, args, access}` (host only), `POST /mcp/servers/{id}/refresh` `{confirm: true}`, `POST /mcp/servers/{id}/remove` `{confirm: true}`, `POST /mcp/servers/{id}/env` `{confirm: true, name, value}` and `POST /mcp/servers/{id}/env/remove` `{confirm: true, name}` (host only; the value is stored in the vault and never returned) |
| Autostart | `GET /autostart`, `POST /autostart/enable` `{confirm: true, start_now}`, `POST /autostart/disable` |
| Search | `GET /search`, `POST /search/install` `{confirm: true}`, `POST /search/remove` `{confirm: true}`, `POST /search/use` `{confirm: true, enabled}` (install, remove and the switch are host only) |
| Power | `GET /power`, `POST /power/stop` `{scope: "jig" \| "jig_and_model", confirm: true}` (202, then a graceful shutdown) |
| Model server | `GET /model`, `POST /model/stop` `{confirm: true}`, `POST /model/start` (only a server Jig launched) |
| Remote access | `GET /remote`, `POST /remote/enable` `{confirm: true}` (host only), `POST /remote/disable` `{confirm: true}` |
| Devices | `GET /devices`, `POST /devices/pairing` `{expires_in_days}` (host only; code, link and QR code), `DELETE /devices/{id}` |

### Access and the API token

The API binds to `127.0.0.1`, and every endpoint except `GET /health` and the UI's static files needs authentication, including `/events`, `/events/sse` and `/approvals`.

- **The token.** On first start Jig generates a random 256-bit token and saves it as `<data_dir>/api-token`. On Windows the file's ACL is cut down to your own account (inheritance removed) and then read back and checked; on other systems it is mode `0600`. If the permissions cannot be set or are looser than that, Jig refuses to start. `jig token show` prints it and `jig token rotate` replaces it; a rotation takes effect at once, signs out every browser session and revokes every paired device.
- **Programs** send `Authorization: Bearer <token>`, on the WebSocket handshake too. `jig chat`, `jig ui` and the scripts in `scripts/` read the token file themselves.
- **The browser** never holds the token, and nor does Jig's window. `jig ui` asks the API for a one-time login code (valid for 2 minutes, single use) and opens `http://127.0.0.1:8766/#code=...`. The code is in the URL fragment, which the browser never sends to the server, so it does not appear in logs. The page exchanges it for an `HttpOnly`, `SameSite=Strict` session cookie (an HMAC of the token with a 12-hour expiry) and removes the fragment from the address bar. You can also paste the token into the sign-in dialog. Requests that use the cookie and change something, and WebSocket handshakes that use it, must come from the UI's own origin, which stops other sites and pages from driving the API.
- **Other devices** (over Tailscale, see [Use Jig from your other devices](#use-jig-from-your-other-devices)) use a paired device session instead: a separate, revocable cookie with a `Secure` flag. The bearer token and the session cookie are refused there.
- **WebSockets** are authenticated during the handshake, either by the bearer header or by a cookie plus the `Origin` check. The token is never put in a query string. A rejected handshake is closed with code 1008.
- **`/health` is public** so supervisors and container health checks can probe Jig without a secret. It returns only `{"status": "ok"}`. The model endpoint, model name and capability results are under `GET /status`, which needs authentication.

## Roadmap

- **A VM sandbox** (for example Firecracker or Hyper-V) as a stronger alternative to the container backend, and per-task egress leases.
- **A mobile app** that connects to Jig running on your own computer, over the same paired-device, Tailscale-only connection the web UI uses, so you can take Jig with you while the model stays at home.
- **Voice**: local speech-to-text and text-to-speech, driving the avatar's `talking` state.
- **A shared, Google-verified Google app** so nobody has to make their own, for the Google connectors.
- **Smaller-GPU support**: measured setups for 8 GB cards, with one model as both agent and safety checker (a different safety checker model stays optional via `[sentinel]`).
- **Multiple agents**, each with its own sandbox, memory and rules.
- Embeddings-backed memory search.
- **Bulk testing with published metrics**: run every scenario (research, coding, background jobs, memory, safety and each connector) many times on real models and services, and publish pass rates, failure kinds and timings per model.
- **Agentic benchmarks**: run recognised agent benchmarks with the same local model twice, once on its own and once inside Jig, to measure how much the harness adds over the base model.
- **Sound cues**: quiet, optional sounds for the moments that need you (an approval waiting, a background task finished, a run stopped), with one switch in Settings to turn them off.

## Licence

Apache License 2.0. See `LICENSE`.

SearXNG, the optional search program, is not part of Jig. It is AGPL-3.0-or-later, a separate program, and Jig downloads a pinned release only when you choose Install search.
