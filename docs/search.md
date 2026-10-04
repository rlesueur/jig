# Search (optional)

Jig can look things up on the web through [SearXNG](https://docs.searxng.org/), a separate program. SearXNG is **not part of Jig** and is not in the installer. It is free software under the GNU Affero General Public License (AGPL-3.0-or-later). Jig itself is Apache-2.0. Jig downloads a pinned upstream release only when you choose **Install search**, into its own folder and its own Python environment, never into this repository.

This is a beta (version 0.1.0b2). Search is optional. Without it, Jig says search is not available. It does not scrape DuckDuckGo or any other engine, and it does not invent results.

The same steps are on **Settings > Search**. Installing, removing and the **Use search** switch only work on the computer Jig runs on. From another device Jig answers that the change only works on the host computer itself, not over the tailnet.

Nothing leaves this machine except the searches SearXNG itself sends to the search engines it uses. Jig reads SearXNG's JSON results (`format=json` on SearXNG's own `/search`).

## Set up search

1. **Install search.** In **Settings > Search**, choose **Install search**. Jig checks whether SearXNG is already running on this computer. If it is, on `127.0.0.1` port 8090, Jig uses that copy and does not install a second one. Otherwise it downloads the pinned release, unpacks it, creates a Python environment for it, and checks that it starts. Install search turns **Use search** on.

   The download is the pinned release `2026.10.2-19ffbcd30` (SearXNG does not publish GitHub release tags, so Jig pins a commit and the checksum of that commit's source archive). It needs about 400 MB free. If the download cannot be fetched, or the file does not match the checksum, Jig says so and leaves nothing installed.

   Jig starts that copy when you ask it to look something up, and stops it when Jig stops. It does not start when you sign in to Windows. It listens on this computer only: `127.0.0.1`, port 8090, or another free local port if 8090 is taken.

2. **Turn on Use search.** The switch is on unless you have turned it off, and Install search turns it on. Leave **Use search** on so Jig looks things up through SearXNG. When it is off, Jig says search is turned off and does not look elsewhere.

3. **Check it works.** The status line on **Settings > Search** says whether search is installed, turned off, or already running. Ask Jig to look something up. Or, in a terminal:

   ```powershell
   jig search status
   ```

   That prints the status line, and the folder and address when Jig has installed a copy. `jig search status --json` includes the licence. When a copy is installed, it also includes the pinned version.

## Turn search off

Turn **Use search** off, or run:

```powershell
jig search use off
```

Jig keeps the installed copy and does not start it. Turn it back on with the switch, or `jig search use on`.

`jig search use` without `on` or `off` does nothing and prints the two forms. Turning search on or off asks you to confirm (`[y/N]`), unless you pass `--yes`.

## Remove search

1. Choose **Remove search** in **Settings > Search**, or run `jig search remove`.
2. That deletes the copy Jig installed, and stops it if Jig started it.
3. If Jig was using a SearXNG that was already running, it does not delete that program and does not stop it. The status line says so.

Removing asks you to confirm on the command line (`[y/N]`), unless you pass `--yes`.

## From the command line

These are the same actions as Settings. On a source install, run `.\.venv\Scripts\jig` (or `.venv/bin/jig`) instead of `jig`. Use the same `--config` as the Jig you use. `--data-dir` picks a data folder. If that folder's Jig is already running, the command talks to it. If it is not, the command does the work itself.

```powershell
jig search status
jig search install
jig search use on
jig search use off
jig search remove
```

`--yes` confirms install, remove, or the switch without the prompt. `--json` prints the machine-readable status. With no terminal to confirm on, and without `--yes`, Jig changes nothing and says to re-run with `--yes`.

While an install is running, `jig search status` (against that running Jig) shows the current step, such as downloading, unpacking, creating the Python environment, or checking that SearXNG starts.

## Where it is installed

- **Windows installer:** `%LOCALAPPDATA%\Jig\searxng`.
- **A data folder outside the Jig checkout:** `<data folder>\searxng` (on Mac and Linux, `<data folder>/searxng`).
- **A data folder inside the checkout** (the default `data` folder when you run from a git clone): a sibling of the checkout, `jig-searxng` plus a short id. The Python environment is kept out of the git repository.

Inside that folder: the unpacked SearXNG source (`src`), its virtual environment (`venv`), Jig's `settings.yml` (loopback only, JSON results on, limiter off so Redis or Valkey is not required), and `state.json`. `settings.yml` holds SearXNG's secret key. Jig does not put that key in the chat.

## If something goes wrong

Jig says what happened, in the status line and, for a failed install, under the buttons. The wording comes from Jig:

- **Search isn't available. SearXNG isn't installed…** Choose **Install search**. Jig has not looked the question up anywhere else.
- **Search is turned off in Settings.** Turn **Use search** on.
- **SearXNG is installed, but it wouldn't start.** The copy is there. Jig does not switch to another engine. Check the status line, then **Remove search** and **Install search** again if you want a fresh copy. A SearXNG you started yourself is left running.
- **SearXNG is running, but it isn't offering JSON results.** Jig only reads JSON. A SearXNG you run yourself needs JSON enabled. The copy Jig installs has JSON on.
- **Couldn't fetch the pinned SearXNG release… Nothing was installed.** The pin could not be downloaded (network, or GitHub). Nothing was left half-installed.
- **the download doesn't match the pinned checksum… Nothing was installed.** The file was deleted.
- **this needs about 400 MB free.** Free some disk space and choose **Install search** again.
- **Jig couldn't find Python to create its own environment.** A source install uses the Python next to `jig`. The Windows installer brings its own.

SearXNG's own page, if you open `http://127.0.0.1:8090/` while it is running, is SearXNG's, not Jig's. Jig does not register it to start at sign-in.

## Licence

SearXNG is AGPL-3.0-or-later, a separate program. Jig downloads a pinned release only when you choose **Install search**. Jig is Apache-2.0. See the README's licence note for the same point.
