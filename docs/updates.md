# Updates

Jig checks for a new version only when you ask it to. It does not check in the background, and it does not install anything unless you agree.

Open **Settings**, then **About and updates**. That page shows the version you are running. Choose **Check for updates**. On Windows you can also choose **Check for updates** on the icon by the clock, which opens the same page. Either way, nothing is downloaded until you go on to install.

Jig asks GitHub, over HTTPS, for the releases of [rlesueur/jig](https://github.com/rlesueur/jig). It is not signed in. If GitHub's limit on unsigned-in requests has been reached, or this computer is offline, Jig says so and stops. Try again later, or when you are back online.

While this copy is a beta (a version such as `0.1.0b1`), the check includes other beta releases. A full release does not offer a beta. Jig never offers an older version than the one you are running. When a newer release exists, the page shows its version, the date GitHub published it, the release notes, and a link to the release page. The notes are shown as text. They are not treated as a web page.

## The Windows installer

If you installed Jig with `JigSetup-<version>.exe`, **Install update** is offered for a newer release that includes `JigSetup-<version>.exe` and a `.sha256` file.

The installer is not code-signed yet, so Windows may warn you if you run a download yourself. An update from this page is different: Jig downloads the installer to a temporary folder and checks it against the SHA-256 and the size published with that same GitHub release. If they do not match, Jig deletes the download, tells you, and does not install it. There is no other file it will try instead.

After that check, and only if you confirmed, Jig finishes what it is doing and turns off, the same way **Turn Jig off** does. A small helper then runs the installer with no wizard (`/VERYSILENT`, and without a reboot). The installer upgrades this copy in place. Your settings, memories and notes stay in `%LOCALAPPDATA%\Jig`. When the installer has finished, Jig opens again. The update is written in **Settings > History**.

Start with Windows is left as it is.

## A checkout or a container

Those copies do not change their own files.

- **Source checkout.** The page tells you to run `git pull`, then `pip install -e .`, and to restart Jig (`jig stop`, then `jig serve`). Your data folder is kept.
- **Container.** The page tells you to run `git pull`, `docker compose pull` and `docker compose up -d` on the computer that runs Docker. That keeps your data volumes. The images for the new version are `ghcr.io/rlesueur/jig:<version>` and `ghcr.io/rlesueur/jig-sandbox:<version>`. See [Updating](container.md#updating).

## What cannot start an update

Checking and installing are buttons in the window, and the check on the tray icon. They are not tools the model can call. The requests need the same sign-in as the rest of Jig, and installing only works on the computer Jig runs on, not from another device.

To look at a different repository (the tests do this), set `[updates] repo = "owner/name"` in `jig.toml`, or set `JIG_UPDATES_REPO`. The usual value is `rlesueur/jig`.
