# Jig demo videos

Reproducible demo recordings of Jig. Each one is a **real run** of the real Jig against the real local
model, with real web pages, real files, real tools and real approvals. It doubles as an end-to-end acceptance
test: a failed check stops the run, and nothing is rendered.

```powershell
cd demos
.\run.ps1 <name> [-Test] [-Format landscape|portrait|all] [-Theme jig-light|jig-dark] [-NoAudio]
.\run.ps1 <name> -RenderOnly [-Capture <stamp>]   # re-render a capture after changing captions or theme
.\run.ps1 launch -Test                             # the five launch demos, one after another
```

The launch set:

| Name | Scenario | What it shows |
| --- | --- | --- |
| `research` | `07-research` | Several official web pages read and cited, a report saved to a file after its approval card (also 4:5) |
| `coding` | `08-coding` | Failing tests, run in Jig's Docker sandbox; Jig finds and fixes the bugs; each run approved (dark theme) |
| `background` | `09-background` | A longer job in "What Jig's up to": planned, worked in the background, paused, resumed, result checked against GOV.UK |
| `memory` | `04-memory` | A preference remembered from chat, shown in Settings, edited, used in a new conversation, forgotten, no longer used |
| `safety` | `03-safety` | An approval card with "Why am I asking?", a No with a note, "Just look, don't touch" preventing a save, History and the audit log (also 4:5) |

Older scenarios (`quickstart`, `compose`, `byo-model`, `always-on`) predate the chat-first redesign and have not
been re-verified against it.

Exit codes: `0` passed, `1` failed, `3` pending (the feature is not in the commit under test).

## How a run works

1. `run.ps1` snapshots the **committed** tree (`git archive` of HEAD, or `-Commit <sha>`) into
   `.work/src-<sha>` and installs it into `.work/venv`, so nobody's uncommitted work is ever filmed.
2. `lib/capture.mjs` starts its own Jig on port **8770** with a fresh temporary data folder and workspace
   (`%TEMP%\jig-demos`), in a real console (ConPTY, `lib/ptybridge.py`), and records:
   - the console's exact output (replayed with xterm.js; the API token is blanked at the exact positions it was printed);
   - the browser (headless Chromium, 1280x720 at 1.5x, light or dark colour scheme, screencast frames with timestamps);
   - the live `/events` stream, used to check that the UI's avatar showed every state the server sent.
   Scenarios can seed the workspace from `fixtures/<name>` and use a different config from `configs/`
   (`demo-sandbox.toml` turns on the Docker code sandbox).
3. The scenario's `edit()` picks the moments to show. `lib/render.mjs` composes the video deterministically
   (captions, chapter titles, crops and rings) and encodes it with ffmpeg: H.264 High, 30 fps, plus a contact
   sheet of 16 stills. Speed-ups are whole numbers with a visible "Sped up" badge; skipped time shows a
   "skipped" badge; cuts never go back in time.
4. Clean-up always runs: the browser, Jig, the sandbox containers, Compose (`down -v`, this project only) and autostart.

Ports: Jig 8770, test site 8771, "nothing listening" 8779. The run refuses to start if any of these are in use,
and never touches 8080 (the model server), 8765, 8766, 8767, 8780 or 8790.

## Restyling

- UI elements are found **only** by accessible role and name, label or data-testid, all in `lib/ui-map.mjs`.
  If the redesign renames something, change it there.
- Caption, chapter, badge and card styling is a theme: `themes/jig-light.css` and `themes/jig-dark.css` use the
  web UI's own tokens and Nunito. Layout per format is in `lib/formats.mjs`.
- `node lib/frames-at.mjs <scenario> <mark>[+ms] ...` copies the captured frames at marks into `out/<scenario>/review/`.

## Requirements

Node 20+, Python 3.11+, Docker Desktop (for `coding` and `compose`), ffmpeg (winget `Gyan.FFmpeg`), and the model
server on `127.0.0.1:8080`. `run.ps1` installs the rest: `npm install` (Playwright, xterm.js, ws) and the venv
(pywinpty, numpy, scipy).

Outputs (`out/`), captures (`captures/`) and `.work/` are not committed. What the recordings found is in
[FINDINGS.md](FINDINGS.md).
