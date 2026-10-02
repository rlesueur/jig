# Findings from the demo recordings

The demo runs double as end-to-end acceptance tests of the real Jig against the real local model, real web
pages, real files and real tools. This file records what they found, as it happened: passes, failures, bugs,
rough edges and safety observations. Genuine Jig bugs found by the launch demos were fixed in focused commits
with tests; each entry says which.

`demos/run.ps1` always tests a clean `git archive` of a commit, not the working tree. Commit and model are
given per run. The model was always the one served on `127.0.0.1:8080`: `bonsai-2-27b` (4 slots, 64k context).

## Launch demos, 2 October 2026

Final takes: commit `3adbfa7`, model `bonsai-2-27b` (max_tokens 8192, temperature 1.0, top_p 0.95, as in the
user's own `jig.toml`), Jig on port 8770 with a throwaway data folder and workspace. Times are wall-clock for the
real run, from sending the message (or creating the goal) to the run finishing. Videos are in `demos/out/<scenario>/`
and copied to `promo/making-of/10-demos/`.

| Scenario | Capture (UTC) | Checks | Jig's real run | Video |
| --- | --- | --- | --- | --- |
| Research | 11:29 | Passed 18/18 | 60 s, 3 steps: read the three official railcard pages, compared the cards citing the address of each, asked before saving `railcards.md` (approved), report shown as the real file. | 53.1 s 16:9; 42.7 s 4:5 |
| Coding | 11:12 | Passed 16/16 | 77 s, 7 steps, 3 approved sandbox commands: tests failed (4 errors), Jig fixed both bugs in `report.py`, tests passed, ran the report. Re-checked outside Jig: `FAILED (errors=4)` before, `OK` after. | 52.4 s 16:9 (dark) |
| Background | 11:25 | Passed 11/11 | Goal 134 s, two tasks (read-only research, then the note); paused and resumed mid-task; every date matched GOV.UK's own data, fetched independently. 1 pass in 5 tries on this commit (F7). | 42.0 s 16:9 |
| Memory | 10:58 | Passed 24/24 | Stored "vegetarian" and "under 30 minutes" (2 s); listed in Settings at once; edited to vegan; a new chat suggested a vegan tofu noodle bowl (7 s); after forgetting, garlic and chilli pasta with Parmesan (3 s); edit and forget in the audit log. | 47.2 s 16:9 |
| Safety | 10:56 | Passed 19/19 | Save asked first; "Why am I asking?" opened; denied with a note, no file written, note stored (27 s). In "Just look, don't touch" no write tools were offered and no file was written (33 s, 9 steps; F6). Audit shows the denial with its note. | 62.8 s 16:9; 61.6 s 4:5 |

### Every attempt on the day (all real runs; none hidden)

Times are UTC. "Test" takes carry a "Test take" watermark and are not for publication.

| Time | Scenario | Commit | Result | What happened |
| --- | --- | --- | --- | --- |
| 09:54 | Research | `cebd668` | Test, passed 16/16 | 71 s. Replies shown as raw Markdown (F4). |
| 10:00 | Research | `d55ffec` | Test, passed 16/16 | 81 s, 4 steps, one guessed address 404 (F9). |
| 10:03 | Coding | `d55ffec` | Test, passed 13/13 | Jig wrote `report.py` from scratch and the tests passed first time, so nothing to fix; console check was bogus (F11). |
| 10:08 | Safety | `9e8dde8` | Test, passed 19/19 | Read-only: Sentinel denied an unrelated fetch; Jig wrote a private note (F6). |
| 10:11 | Memory | `8ef96ec` | Failed (my check) | Jig stored two memories; the check expected one. Relaxed. |
| 10:15 | Memory | `8ef96ec` | Failed (Jig bug) | After editing the memory to vegan, a new chat suggested roast chicken (F5). Fixed in `56400be`. |
| 10:22 | Memory | `56400be` | Test, passed 24/24 | Plant-based curry, then grilled salmon after forgetting. |
| 10:24 | Background | `56400be` | Failed (my locator) | Jig's tasks finished; the script's goal-result locator matched five elements. |
| 10:26 | Background | `56400be` | Failed (my check) | Goal done; the script expected the avatar to show "paused" while another task was running. Check removed. |
| 10:31 | Background | `56400be` | Failed (model) | Research task cut off at max_tokens after reading the bank-holidays file (F7). |
| 10:35 | Background | `56400be` | Failed (my check) | Goal done and dates right; "Fri 25 Dec" was rejected by a too-strict matcher. |
| 10:39 | Background | `56400be` | Passed 11/11 | Results shown as raw Markdown (F10), so not used. |
| 10:43 | Coding | `3adbfa7` | Stopped by me | Stopped during setup to change the pipeline; Jig was not asked anything. |
| 10:51 | Coding | `3adbfa7` | Failed (pipeline) | Console had no `PATH`; `python` not found (F11). |
| 10:52 | Background | `3adbfa7` | Failed (model) | "Write the diary note" cut off at max_tokens after re-reading the bank-holidays file (F7). |
| 10:56 | Safety | `3adbfa7` | **Final**, passed 19/19 | See table above. |
| 10:58 | Memory | `3adbfa7` | **Final**, passed 24/24 | See table above. |
| 11:00 | Research | `3adbfa7` | Failed (model) | 70 s. The reply named its sources but gave no web addresses, although the brief asked for them; `railcards.md` had them. |
| 11:04 | Coding | `3adbfa7` | Failed (pipeline) | Same `PATH` fault; found and fixed after this run (F11). |
| 11:04 | Background | `3adbfa7` | Failed (model) | Writing step cut off at max_tokens (F7). |
| 11:09 | Research | `3adbfa7` | Passed 16/16 | 84 s. Not used: the console showed the report with garbled `£` and dashes (F11). |
| 11:12 | Coding | `3adbfa7` | **Final**, passed 16/16 | See table above. |
| 11:14 | Background | `3adbfa7` | Failed (model) | The bank-holidays task cut off at max_tokens, this time without being paused (F7). |
| 11:20 | Background | `3adbfa7` | Failed (model) | Writing step cut off at max_tokens (F7). |
| 11:25 | Background | `3adbfa7` | **Final**, passed 11/11 | See table above. |
| 11:29 | Research | `3adbfa7` | **Final**, passed 18/18 | See table above. Adds the check that every cited address is one Jig fetched successfully. |

## F7. Background research task cut off at the model's output limit (capability, not a Jig bug)

- **Commits:** `56400be` and `3adbfa7` (they differ only in the web UI). **Severity:** medium for long background
  jobs; the most frequent failure of the day.
- **What happened:** in 5 of the 10 runs of the background goal ("next three bank holidays in England and Wales
  and in Scotland, and when the clocks go back, from GOV.UK"), a model call made after reading
  `https://www.gov.uk/bank-holidays.json` (every bank holiday since 2018, for three divisions) generated for 80 to
  120 s until it hit `max_tokens` (8192). Twice this was in the task that read the file (10:31, 11:14); three times
  in "Write the diary note": at 10:52 in its very first call, and at 11:04 and 11:20 after it had fetched the
  file again itself instead of using the earlier task's result. Jig failed the task with
  `ModelError: agent model output was cut off at max_tokens (8192); raise max_tokens or shorten the task`, blocked
  or failed what depended on it, and failed the goal. The other five runs read the same file and finished with
  correct dates.
- **Pause and resume were not the cause:** in 10:31 the task had been paused during that call and resumed six
  seconds later, but in 11:14 the task that was cut off had never been paused, and in 11:04 and 11:20 the paused
  task finished and a later one was cut off. Resuming re-runs the interrupted call with the same checkpointed
  messages (`jig/agent/loop.py`).
- **Jig's handling was correct** (an honest failure, shown in "What Jig's up to" with the reason). No retry or
  fallback was added. Options for a decision: a larger `max_tokens` for background tasks, or a `web_fetch` that
  summarises large JSON.

## F10. Task, goal and schedule results were shown as raw Markdown (fixed in `3adbfa7`)

- **Commit:** found on `56400be` (background take); fixed in `3adbfa7` ("Web UI: show task, goal and schedule
  results as formatted text, like Jig's replies"), with `tests/test_ui_results.py` (real `jig serve`, real model,
  real browser; fails without the change).
- **What happened:** the goal's result in "What Jig's up to" showed `**England and Wales**` and `- ` literally.
  Results are now formatted the same way as chat replies; errors stay as plain text.

## F11. Demo pipeline faults found while recording (not Jig bugs)

- **Consoles had no `PATH`:** a scenario passing its own `env` replaced the console's whole environment, so
  `python` was "not recognised" and, because PowerShell sets no exit code for an unknown command, the "tests fail
  before Jig starts" and "tests pass outside Jig" console checks read exit code 0. The coding test take on
  `d55ffec` therefore passed its "verified outside Jig" check without verifying anything. Fixed; the final coding
  take shows the real `FAILED (errors=4)` and then `OK`.
- **False command completion:** PSReadLine repaints replayed the prompt marker, so a command could look finished
  before it ran. Prompts are now numbered and only a newer prompt counts.
- **`run.ps1 -Commit`** checked a stale exit code; fixed.
- **Console text clipped:** the console's font size was estimated from a line-height ratio that is too small for
  the font, so the last two rows (including `OK`) were cut off. It now measures the rendered height.
- **Garbled `£` and dashes** when showing the report: Windows PowerShell 5 reads files without a byte order mark
  as ANSI; the demo now uses `Get-Content -Encoding UTF8`.

## F6. In "Just look, don't touch", Jig still writes its own private notes

- **Commit:** `9e8dde8` (safety test take). **Severity:** low; a wording question.
- **What happened:** with "Just look, don't touch" on, asked "Now save the list to shopping.md, and add rice to
  it", Jig correctly did not write the file (write tools are not offered in read-only mode, and the core rule would
  refuse them). It first tried to fetch an unrelated public page, which Jig's safety check (Sentinel) denied as
  not serving the request (risk medium), then saved the list with `note_write` (effect `private_write`, allowed
  in read-only mode by design) and told the user it had "noted the complete list in a private note".
- **Why it matters:** the note under the message box says "Just looking: Jig won't change, save or send
  anything", but private notes and memories are still saved. Either the wording or the mode needs a decision.
  The demo caption says only what is true: Jig "won't touch your files or send anything".

## F5. A new conversation ignored saved memories (fixed in `56400be`)

- **Commit:** found on `8ef96ec`; fixed in `56400be` ("Memory: give every conversation and task the user's saved
  memories, so preferences shape everyday requests"), with tests in `tests/test_memory.py`.
- **What happened:** told "I'm vegetarian, and on weeknights I want dinners that take under 30 minutes", Jig stored
  it with `memory_add`. After the memory was edited to "vegan" in Settings, a new conversation asking "Suggest one
  dinner for tonight" got "A lemon-herb roast chicken with buttery potatoes...". The run made no memory call at
  all: the system prompt never mentioned memory, and the model does not think to search it for an everyday request.
- **Fix:** the system prompt for every conversation and task now ends with the saved memories (newest first,
  at most 50 and about 6,000 characters, each cut at 500), marked as information rather than instructions; edits
  and forgets apply from the next conversation. After the fix the same request got a plant-based curry, and after
  the memory was forgotten, grilled salmon.

## F4. Jig's replies were shown as raw Markdown (fixed in `d55ffec`)

- **Commit:** found on `cebd668`; fixed in `d55ffec` ("Web UI: show Jig's replies as formatted text (lists, bold,
  tables, links with their full address), never as HTML"), with `tests/web/markdown.test.mjs`.
- **What happened:** the redesigned chat showed the model's Markdown literally (`**Price:**`, `| --- |`, `###`),
  which made the research comparison hard to read. Replies are now parsed into lists, headings, tables, code and
  links (http and https only, each shown with its full address), built with DOM nodes and text only, never HTML.

## F8. No way to schedule from chat, and an unbacked "noted" claim

- **Commit:** `d55ffec` (probe). **Severity:** medium (a claim with nothing behind it).
- **What happened:** asked to check a page every morning, Jig said it cannot poll on its own (true for that commit:
  schedules existed only through the REST API `/schedules`, with no agent tool, UI or CLI), but also said it had
  "noted this as the baseline" although no note, memory or file was written in that run.
- **Note:** an approved `schedule_create` tool and Settings > Schedules have since landed in `4ed6fa3` (another
  workstream). They are in the final commit but were not exercised by these demos.

## F9. Smaller observations

- **Research:** in most research runs Jig first guessed `railcard.co.uk/railcards/family-friends-railcard/`, which
  returns 404, reported "Something went wrong while reading a web page", then found and read the real page. The
  `/railcards/...` addresses for the other two cards redirect to `/two-together-railcard/` and `/16-25-railcard/`,
  which are the addresses Jig cited (checked by fetching each one). In the final take it went straight to the
  three real pages. The facts in the report matched the pages.
- **Planner:** the background goal was planned differently each time (two to four tasks), sometimes with a
  separate "Get the current date" task although the date is in the system prompt; harmless but a wasted step. In
  the final take the plan's summary line, shown above the goal's result, reads "This is not required by user,
  ignore", which is meaningless. The video's close-up frames the result itself, below that line; the line is
  visible, small, in the wider shots.
- **Memory:** the model sometimes stores one combined memory, sometimes one per fact. Both are reasonable; the
  demo works with either.
- **Coding:** when asked to write `report.py` from scratch, Jig's first version passed the tests in one take
  (13/13 checks), so there was no failing test to fix. The demo now seeds a script with two real bugs (prices
  with thousands separators crash it; totals lack thousands separators), and Jig fixes them.
- **Replies contain em dashes and full web addresses.** These are the model's own words and are shown as they
  are; the demos' own captions and cards use neither.

## F1. Ctrl+C on `jig serve` waits on idle `/events` WebSockets (10 s and a traceback; it hung on older commits)

- **Commits:** `61a87f2` (hangs indefinitely), `f014791` (bounded at about 10 s, with an `ERROR` traceback).
  Not re-checked on the launch commits; not fixed.
- **Severity:** low (shutdown is slow and noisy whenever the web UI is or was open).
- **What happens:** with any `/events` WebSocket open, or one whose client has already gone away, Ctrl+C prints
  `Waiting for background tasks to complete. (CTRL+C to force quit)`. On `61a87f2` it then waits forever (killed
  after 30 s in the test). On `f014791` the new graceful-shutdown timeout cancels it after about 10 s and logs
  `ERROR: Exception in ASGI application ... asyncio.exceptions.CancelledError: Task cancelled, timeout graceful
  shutdown exceeded` with a full traceback ending in `jig/api/app.py` `events_ws` at `event = await sub.get()`.
  Without a WebSocket, the same shutdown takes 0.4 s.
- **Cause (from the traceback):** `events_ws` only sends; it never receives, so it does not notice that the client
  has disconnected, and nothing wakes it on shutdown. It waits in `sub.get()` until an event arrives.
- **Reproduce** (measured with `demos/.work/ctrlc-hang.mjs`, which uses the same real console as the demos):
  1. `jig serve` in a console.
  2. Connect to `ws://127.0.0.1:<port>/events` with `Authorization: Bearer <token>`, then close the client.
  3. Press Ctrl+C in the console. Result: about 10.5 s and the traceback above (no WebSocket: 0.4 s, clean).
- **Measured on `f014791`:** no WebSocket 0.41 s; WebSocket still connected 10.52 s; WebSocket already closed 10.52 s.

## F3. The Memory panel did not update when the agent remembered or forgot something (fixed in `cebd668`)

- **Commit:** found on `29a6b2f`; fixed in `cebd668` ("Memory: publish memory.changed for every change, including
  the agent's own memory tools"), with `test_every_memory_change_is_published`.
- **What happened:** the agent's `memory_add` and `memory_forget` tools changed memory without publishing
  `memory.changed`; only the REST routes did. The UI refreshes its list on that event, so it stayed stale until
  **Show all**, a search, or a reload. The memory demo now checks that the new memory appears in Settings without
  pressing anything, and it does.

## F2. The UI's autostart row shows a raw Task Scheduler code before the first run

- **Commit:** `8e714e6`. **Severity:** cosmetic. Not re-checked against the redesigned Settings.
- **What happens:** right after `jig autostart enable`, the Status card reads
  `On (last result 0x41303 (has not run yet))`. `jig/web/autostart.js` shows `not run yet` only when
  `status.last_result` is empty, but on Windows the backend fills it with `0x41303 (has not run yet)`
  (`SCHED_S_TASK_HAS_NOT_RUN`), so the friendly branch never shows and the hex code reaches the user.
- **Reproduce:** `jig autostart enable` (answer `y`, without `--now`), open the web UI, look at Status → Autostart.
  `jig autostart disable` afterwards. Seen in the `06-always-on` capture of 2026-10-01 23:16 UTC.
