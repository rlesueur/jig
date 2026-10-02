# Jig demo videos

Reproducible demo recordings of Jig. Each one is a **real run** of the real Jig against the real local
model, with real tools and real approvals. It doubles as an end-to-end acceptance test: a failed check stops
the run, and nothing is rendered.

```powershell
cd demos
.\run.ps1 <name> [-Test] [-Format landscape|portrait|all] [-Theme neon|warm] [-NoAudio]
.\run.ps1 <name> -RenderOnly [-Capture <stamp>]   # re-render a capture after changing captions or theme
.\run.ps1 all -Test
```

| Name | Scenario | What it shows |
| --- | --- | --- |
| `quickstart` | `01-quickstart` | `jig health`, `jig serve`, `jig token show`, sign in, status, a first chat |
| `compose` | `01-quickstart-compose` | The Docker Compose quick start, typed as documented, then the same UI |
| `goal` | `02-goal` | A goal planned into tasks, background research, approval in the inbox, the result file (also 4:5) |
| `safety` | `03-safety` | A hidden prompt injection on a real public page, a local address blocked, a custom rule (ask, then block), the audit log |
| `memory` | `04-memory` | Remember from chat, see, edit, recall, forget, gone |
| `byo-model` | `05-byo-model` | Profiles, the config, `jig health` passing and failing against an empty port |
| `always-on` | `06-always-on` | `jig autostart` status, enable (disclosure and y/N), disable, status; left disabled |

Exit codes: `0` passed, `1` failed, `3` pending (the feature is not in the commit under test).

## How a run works

1. `run.ps1` snapshots the **committed** tree (`git archive` of HEAD, or `-Commit <sha>`) into
   `.work/src-<sha>` and installs it into `.work/venv`, so nobody's uncommitted work is ever filmed.
2. `lib/capture.mjs` starts its own Jig on port **8770** with a fresh temporary data folder (`%TEMP%\jig-demos`),
   in a real console (ConPTY, `lib/ptybridge.py`), and records:
   - the console's exact output (replayed with xterm.js; the API token is blanked at the exact positions it was printed);
   - the browser (headless Chromium, 1280x720 at 1.5x, screencast frames with timestamps);
   - the live `/events` stream, used to check that the UI's avatar showed every state the server sent.
3. The scenario's `edit()` picks the moments to show. `lib/render.mjs` composes the video deterministically
   (captions, chapter titles, crops and rings) and encodes it with ffmpeg: H.264 High, 30 fps.
   Speed-ups are whole numbers with a visible "sped up" badge; skipped time shows a "skipped" badge; cuts never go back in time.
4. Clean-up always runs: the browser, Jig, test web servers, the tunnel, Compose (`down -v`, this project only) and autostart.

Ports: Jig 8770, test site 8771, "nothing listening" 8779. The run refuses to start if any of these are in use,
and never touches 8080 (the model server), 8765, 8766, 8767, 8780 or 8790.

## Restyling

- UI elements are found **only** by accessible role and name, label or data-testid, all in `lib/ui-map.mjs`.
  If the redesign renames something, change it there.
- Caption, chapter, badge and card styling is a theme: `themes/neon.css` (the promo look) and `themes/warm.css`
  (a draft to replace with the chosen UI palette). Layout per format is in `lib/formats.mjs`.

## Requirements

Node 20+, Python 3.11+, Docker Desktop (for `compose` only), ffmpeg (winget `Gyan.FFmpeg`), and the model
server on `127.0.0.1:8080`. `run.ps1` installs the rest: `npm install` (Playwright, xterm.js, ws), the venv
(pywinpty, numpy, scipy) and, for `safety`, the standalone `cloudflared` binary, checked against GitHub's
published SHA-256.

Outputs (`out/`), captures (`captures/`) and `.work/` are not committed. Bugs found while recording are in
[FINDINGS.md](FINDINGS.md).
