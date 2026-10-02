# Findings from the demo recordings

The demo runs double as end-to-end acceptance tests of the real Jig against the real local model. This file
records what they found: bugs, rough edges and safety observations, reported as they happened. Nothing here
has been fixed by the demo work (the demos never edit `jig/`); each entry has steps to reproduce.

Commit under test is given per finding. `demos/run.ps1` always tests a clean `git archive` of a commit, not
the working tree.

## F1. Ctrl+C on `jig serve` waits on idle `/events` WebSockets (10 s and a traceback; it hung on older commits)

- **Commits:** `61a87f2` (hangs indefinitely), `f014791` (bounded at about 10 s, with an `ERROR` traceback).
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

## F3. The Memory panel does not update when the agent remembers or forgets something

- **Commit:** `29a6b2f`. **Severity:** medium (the user is told "Jig hasn't remembered anything yet" right after Jig
  says it has remembered something).
- **What happens:** the agent's `memory_add` and `memory_forget` tools (`jig/tools/builtin.py`) change memory
  without publishing `memory.changed`; only the REST routes in `jig/api/app.py` publish it. The UI refreshes the
  panel on that event, and `selectTab()` in `jig/web/app.js` does not reload it when the Memory tab is opened. So
  the panel stays stale until **Show all**, a search, or a page reload.
- **Reproduce:** open the UI (Memory shows "Jig hasn't remembered anything yet"). In Chat, send
  "Please remember that my sister's birthday is on 14 March." The run calls `memory_add` (see `tool.start` on
  `/events`) and `GET /memory` returns the new row. Open the Memory tab: still empty. Press Show all: it appears.
- The demo always presses Show all, and each capture notes whether the panel was stale before it.

## F2. The UI's autostart row shows a raw Task Scheduler code before the first run

- **Commit:** `8e714e6`. **Severity:** cosmetic.
- **What happens:** right after `jig autostart enable`, the Status card reads
  `On (last result 0x41303 (has not run yet))`. `jig/web/autostart.js` shows `not run yet` only when
  `status.last_result` is empty, but on Windows the backend fills it with `0x41303 (has not run yet)`
  (`SCHED_S_TASK_HAS_NOT_RUN`), so the friendly branch never shows and the hex code reaches the user.
- **Reproduce:** `jig autostart enable` (answer `y`, without `--now`), open the web UI, look at Status → Autostart.
  `jig autostart disable` afterwards. Seen in the `06-always-on` capture of 2026-10-01 23:16 UTC.
