# Running Jig in containers

Jig ships as two images on GitHub Container Registry and a Docker Compose file:

| Image | Contents | Platforms |
| --- | --- | --- |
| `ghcr.io/rlesueur/jig` | The runtime, API, web UI and avatar (Python 3.13 slim, about 270 MB) | linux/amd64, linux/arm64 |
| `ghcr.io/rlesueur/jig-sandbox` | The sandbox: Python, Playwright and Chromium (about 3.7 GB) | linux/amd64, linux/arm64 |

Both run as the unprivileged user `10001:10001`. Jig is model-agnostic in a container too: it talks to whatever OpenAI-compatible server you point it at, and nothing in the images names a model.

- [Quick start](#quick-start)
- [What runs](#what-runs)
- [Configuration](#configuration)
- [The model server](#the-model-server)
- [Security model](#security-model)
- [Vault key](#vault-key)
- [Signing in](#signing-in)
- [Always on](#always-on)
- [Updating](#updating)
- [Backups](#backups)
- [Troubleshooting](#troubleshooting)

## Quick start

You need Docker (Docker Desktop on Windows and macOS, or Docker Engine with the Compose plugin on Linux) and a model server with a tool-capable model. By default, that server runs on the host on port 8080.

Linux and macOS:

```sh
git clone https://github.com/rlesueur/jig.git && cd jig
openssl rand -base64 32 > secrets/jig_vault_key          # once; keep a copy somewhere safe
docker compose up -d && docker compose exec jig jig token show
```

Windows (PowerShell):

```powershell
git clone https://github.com/rlesueur/jig.git; cd jig
$b = [byte[]]::new(32); [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
[IO.File]::WriteAllText("$PWD\secrets\jig_vault_key", [Convert]::ToBase64String($b))
docker compose up -d; docker compose exec jig jig token show
```

Then open <http://127.0.0.1:8766> and paste the token into the sign-in dialog. `docker compose logs -f jig` shows Jig waiting for the model, running its capability checks and checking the sandbox. If any check fails, Jig exits with the reason and compose restarts it.

To build the images from your checkout instead of pulling them, run `docker compose build` first, or `docker compose up -d --build`.

## What runs

```
                 host 127.0.0.1:8766
                         │ (published on loopback only)
  ┌──────────────────────┼────────────────────────── network "default" (normal bridge) ──┐
  │                      ▼                                                               │
  │   ┌──────────────────────────────┐        model server: on the host                  │
  │   │ jig                          │──────► (host.docker.internal:8080), or the        │
  │   │ API, UI, scheduler, Sentinel │        ollama / llamacpp service                  │
  │   │ egress proxy :3128 ──────────┼──────► public internet (only for leased,          │
  │   └──────────────┬───────────────┘        policy-checked connections)                │
  └──────────────────┼────────────────────────────────────────────────────────────────────┘
  ┌──────────────────┼──────────────── network "sandbox" (internal: true, no route out) ─┐
  │      ┌───────────┴────────────┐                                                      │
  │      ▼                        ▼                                                      │
  │  sandbox-exec :7010      sandbox-browser :7011        both mount the "workspace"    │
  │  run_command, run_python browser_* (Chromium)          volume at /workspace          │
  └───────────────────────────────────────────────────────────────────────────────────────┘
```

| Service | Image | Networks | Notes |
| --- | --- | --- | --- |
| `jig` | `jig` | `default`, `sandbox` | Port 8766 published on `127.0.0.1` only. Volumes `jig-data` (database, audit, memory, API token) and `workspace`. The vault key arrives as the Docker secret `jig_vault_key`. |
| `sandbox-exec` | `jig-sandbox` | `sandbox` only | Runs commands and code for `run_command` and `run_python`. |
| `sandbox-browser` | `jig-sandbox` | `sandbox` only | Headless Chromium for the `browser_*` tools. |
| `ollama` | `ollama/ollama` | `default` | Profile `ollama` only. |
| `llamacpp` | `ghcr.io/ggml-org/llama.cpp:server-cuda` | `default` | Profile `llamacpp` only. |

| Volume | Holds |
| --- | --- |
| `jig-data` | `/var/lib/jig/data`: `jig.db` (goals, tasks, memory, rules, audit log, vault ciphertexts and the vault key check) and `api-token` |
| `workspace` | The agent's files, shared by Jig's file tools and both sandbox services |
| `ollama` | Ollama's models (profile `ollama`) |

## Configuration

The image contains `deploy/jig.toml` at `/etc/jig/jig.toml`, and `compose.yaml` mounts the copy in your checkout over it (read-only). To use a different file, set `JIG_CONFIG_FILE` in `.env`. Copy `deploy/env.example` to `.env` to see every variable.

| Variable (in `.env`) | Default | Effect |
| --- | --- | --- |
| `JIG_HOST_PORT` | `8766` | Host port for the UI and API |
| `JIG_BIND_ADDRESS` | `127.0.0.1` | Host address the port is published on. See [Security model](#security-model) before changing it |
| `JIG_MODEL_BASE_URL` | `http://host.docker.internal:8080/v1` | The OpenAI-compatible endpoint |
| `JIG_MODEL_NAME` | empty (discover) | Needed if the server offers more than one model |
| `JIG_CONFIG_FILE` | `./deploy/jig.toml` | Your own config file |
| `JIG_VAULT_KEY_PATH` | `./secrets/jig_vault_key` | Host path of the vault key |
| `JIG_IMAGE`, `JIG_SANDBOX_IMAGE` | `ghcr.io/rlesueur/jig:<version>`, `ghcr.io/rlesueur/jig-sandbox:<version>` | Images to run |

The container-specific settings in `deploy/jig.toml` are:

```toml
[sandbox]
backend = "compose"                    # sandbox services next to Jig, not containers started by Jig
exec_service = "sandbox-exec:7010"
browser_service = "sandbox-browser:7011"
egress_port = 3128                     # must match HTTP(S)_PROXY in compose.yaml

[vault]
backend = "keyfile"
key_file = "/run/secrets/jig_vault_key"

[model.launch]
readiness_timeout_s = 600              # wait for a model that is still loading, then fail loudly

[server]
host = "0.0.0.0"                       # inside the container; compose publishes it on loopback only
```

The sandbox's CPU, memory, process and tmpfs limits are set in `compose.yaml` (`x-sandbox`). In this mode the `[sandbox]` keys `cpus`, `memory`, `pids_limit`, `tmp_size`, `shm_size`, `image` and `egress_bind` are not used.

## The model server

**Default: a server on the host.** Jig uses `http://host.docker.internal:8080/v1`. Docker Desktop provides that name, and `compose.yaml` adds `host.docker.internal:host-gateway` so it works on Linux Docker Engine as well.

- On **Docker Desktop**, a server listening on the host's `127.0.0.1` can be reached this way.
- On **Linux Docker Engine**, `host.docker.internal` is the Docker bridge address, so the server must listen on that address or on `0.0.0.0` (for example `llama-server --host 0.0.0.0`), and your firewall must let the bridge in. Do not expose the model server beyond your machine.

**Profile `ollama`.** This runs Ollama with every NVIDIA GPU and a volume for its models:

```sh
echo 'JIG_MODEL_BASE_URL=http://ollama:11434/v1' >> .env
docker compose --profile ollama up -d
docker compose exec ollama ollama pull <a-tool-capable-model>
```

If you pull more than one model, set `JIG_MODEL_NAME`. `OLLAMA_CONTEXT_LENGTH` defaults to 32768.

**Profile `llamacpp`.** This runs the official llama.cpp server image (CUDA, linux/amd64), with `LLAMACPP_MODELS_DIR` (default `./models`) mounted read-only at `/models`:

```sh
echo 'JIG_MODEL_BASE_URL=http://llamacpp:8080/v1' >> .env
echo 'LLAMACPP_ARGS=-m /models/your-model.gguf --host 0.0.0.0 --port 8080 --jinja --ctx-size 32768 --parallel 4 -ngl 999' >> .env
docker compose --profile llamacpp up -d
```

Special forks are not in the official image. One example is the PrismML fork that some ternary models (such as Ternary Bonsai) need. Keep a fork on the host and use the default setup.

**GPUs.** The profiles reserve NVIDIA GPUs:

- **Linux:** needs the NVIDIA Container Toolkit.
- **Windows:** Docker Desktop with the WSL 2 backend and a current NVIDIA driver provides them.
- **macOS:** Docker cannot use the GPU. Run the model server on the host, for example the Ollama app or llama.cpp with Metal.

**Start-up checks.** Jig waits for the configured endpoint to serve the model, for at most `[model.launch] readiness_timeout_s` (600 s by default). It then runs the real capability checks against it: a tool call and a JSON-schema answer, plus a test image if `[vision] enabled = true`. If the server is not ready in time, or a check fails, Jig exits with the reason and compose restarts it. It never switches to another model or server.

## Security model

**The Jig container.** It runs as uid 10001 with:

- a read-only root filesystem and a small `/tmp` tmpfs;
- all capabilities dropped and `no-new-privileges`;
- **no Docker socket**.

Jig cannot start, stop or inspect containers, and a compromise of Jig does not hand over the host. It listens on `0.0.0.0` inside the container, but compose publishes the port on `127.0.0.1` only. Every endpoint except `GET /health` needs the API token. If you set `JIG_BIND_ADDRESS=0.0.0.0` to reach Jig from other machines, anyone on your network can attempt to sign in, and traffic is plain HTTP. Put an HTTPS reverse proxy in front instead.

**The sandbox services.** Both use the same hardening as the per-agent backend:

- uid 10001, all capabilities dropped and `no-new-privileges`;
- a read-only root, with tmpfs at `/tmp` and `/home/jig`;
- a 256 MB `/dev/shm`;
- 2 GB of memory with no extra swap, 2 CPUs and 512 processes;
- `init`, and Docker's default seccomp profile;
- only the `workspace` volume mounted.

Their only network is `sandbox`, which is `internal: true`, so it has no gateway and no route out. They have no external DNS (`dns: 127.0.0.1`), so only service names resolve.

**Egress.** `HTTP(S)_PROXY` (and Chromium's proxy) in the sandbox point at `jig:3128`. That is Jig's egress proxy, which listens only on Jig's own address on the `sandbox` network. It is the same proxy as in the per-agent backend, with the same rules:

- **Lease.** It allows a connection only while a container tool that has passed the whole gate is running: core rules, custom rules, the Sentinel and any approval.
- **No local network.** It resolves the host itself and refuses loopback, private, link-local and reserved addresses. That covers the model server, the host, Jig and other containers.
- **Ports.** Only `egress_ports` are allowed.
- **Custom rules.** It applies your `egress` rules.

Every decision is audited as `egress.allow` or `egress.block`.

**Jig's API is closed to the sandbox.** Jig has to be on the `sandbox` network to reach the services, so code in the sandbox can open a TCP connection to `jig:8766`. A middleware refuses every request and WebSocket handshake from a sandbox network with 403, before authentication, and audits it as `api.sandbox_peer_refused`.

**The services only serve Jig.** Each sandbox service accepts requests only from the address that `jig` resolves to; loopback may only ping it. It marks itself non-dumpable, so code it runs cannot ptrace it or read its memory.

**Checked at every start.** Jig refuses to start unless all of these hold:

- both services answer;
- each one reports a non-root uid, no capabilities, `no-new-privileges`, a read-only root and a writable `/workspace`;
- neither can connect to `1.1.1.1:443` or `8.8.8.8:53`, or resolve `example.com`;
- both are on a network directly attached to Jig;
- a file Jig writes in its workspace appears in `sandbox-exec`'s `/workspace`;
- a `CONNECT` through the sandbox's own `HTTPS_PROXY` is answered with 403 while no lease is held.

**What this mode gives up**, compared with `backend = "container"` on a host install:

- **One shared sandbox.** The host backend creates one container per agent workspace at start-up and removes it at shutdown. Here there is one `sandbox-exec` and one `sandbox-browser` for the whole stack, and they outlive Jig restarts. Background processes and `/tmp` contents from earlier commands stay until the service restarts (`docker compose restart sandbox-exec`).
- **The command server shares the sandbox.** Commands no longer arrive through `docker exec` from the container runtime. They come through a small server inside the sandbox, running as the same uid as the code it runs. That code cannot read or trace the server, and cannot take over its port, but it can kill it. The container then stops and compose restarts it: a denial of service, not an escape.
- **Jig is a neighbour.** The sandbox can open connections to Jig's API port (refused with 403) and the proxy port (lease-gated). In the host backend, its only neighbour is a relay.
- **Limits live in `compose.yaml`**, not `jig.toml`.
- **Files live in a volume.** The workspace is a Docker volume, not a host folder. Use `docker compose cp jig:/var/lib/jig/sandbox/default/<file> .`, or replace the volume with a bind mount owned by uid 10001.
- The [limits listed in the README](../README.md#container-sandbox-and-headless-browser) still apply: per-action review, not per-connection review; leases per runtime; a shared kernel; and Chromium without its own sandbox.

**Getting per-agent isolation back.** You have two options:

- Run one compose project per agent, for example `docker compose -p jig-work up -d` with its own `.env` and `JIG_HOST_PORT`. Each project has its own networks, volumes, sandbox services and Jig.
- Run Jig on the host with `[sandbox] backend = "container"`, which creates a hardened container per agent workspace.

**No Docker socket mode.** Mounting `/var/run/docker.sock` into Jig would let it create per-agent containers again, but it gives Jig root-equivalent control of the host. The per-agent backend also bind-mounts host paths and reaches its egress proxy through `host.docker.internal`, which assumes Jig runs on the host. So this deployment does not offer a socket mode. If you need per-agent containers, run Jig on the host.

**Autostart.** The host autostart (Task Scheduler, launchd or systemd) is not used in container mode; see [Always on](#always-on).

## Vault key

In a container there is no Windows DPAPI and no OS keyring, so `deploy/jig.toml` selects the explicit `keyfile` backend:

- **Key file.** The key is a file you create: the Docker secret `jig_vault_key`, mounted at `/run/secrets/jig_vault_key`. Its contents, with surrounding whitespace removed, are the key material, and must be at least 32 characters. `openssl rand -base64 32` gives 44.
- **KDF.** scrypt (n=2^15, r=8, p=1) with a random salt derives a 256-bit key.
- **Cipher.** Each secret is encrypted with AES-256-GCM and a fresh nonce. The secret's name is authenticated with it, so a ciphertext cannot be moved to another name. The ciphertexts are stored in `jig.db`.
- **Key check.** The salt and an encrypted check value live in the same database (table `vault_keyfile`), so a wrong key is caught at start-up.

Jig never generates the key, never stores it in the data volume and never falls back to plaintext or to another backend. It refuses to start if any of these is true:

| Problem | Message (abridged) |
| --- | --- |
| No key file on the host (Docker then mounts an empty directory in its place) | `the vault key path /run/secrets/jig_vault_key is a directory, not a key file` |
| The key path does not exist in the container | `the vault key file ... does not exist` |
| An empty key, or a key that is too short | `... is empty` / `... at least 32 are needed` |
| A different key from the one the vault was created with | `the key in ... does not match this vault (its key check failed)` |
| Secrets present but their key check missing | `... no key check (table vault_keyfile)` |

Every message points back here.

**File permissions.**

- **Linux:** the key must be readable by uid 10001, for example `sudo chown 10001:10001 secrets/jig_vault_key && sudo chmod 0400 secrets/jig_vault_key`.
- **Docker Desktop:** the file is readable without that.
- **Everywhere:** keep it out of version control (`secrets/` ignores everything but its own `.gitignore`).

**Back up the key separately from the data volume.** Without the key, the secrets in a backup cannot be recovered; with both, anyone can read them. To change the key, set each secret again (`PUT /vault/{name}`) after starting with the new key on a fresh vault. There is no in-place re-encryption yet.

On a host install the default is still `[vault] backend = "auto"`: DPAPI on Windows, the keyring elsewhere. You can force `dpapi` or `keyring`, or use `keyfile` there too.

## Signing in

The API token is generated on first start into the data volume (`/var/lib/jig/data/api-token`, mode 0600):

```sh
docker compose exec jig jig token show      # print it; paste it into the UI's sign-in dialog
docker compose exec jig jig token rotate    # replace it; signs out every browser and revokes every paired device
```

`jig ui` (the one-time sign-in link) builds the link from `--url`, so run it with the address Jig sees inside the container. Pasting the token is simpler in container mode. Programs send `Authorization: Bearer <token>`.

## Always on

Every service has `restart: unless-stopped`. Jig comes back after a crash, a failed start-up check (for example a model that is not loaded yet), a Docker restart or a reboot, unless you stopped it with `docker compose stop` or `down`.

- **Windows and macOS:** in Docker Desktop, turn on *Settings → General → Start Docker Desktop when you sign in*. Containers start when Docker Desktop does, which is after you sign in, not at boot.
- **Linux:** enable the Docker service with `sudo systemctl enable --now docker`. Containers then start at boot.

Jig's host autostart (Task Scheduler, launchd or systemd) is **not** used in container mode. The image sets `JIG_DEPLOYMENT=container` and `deploy/jig.toml` sets `deployment = "container"`, so Jig knows it runs in a container without guessing. Inside it, `jig autostart enable` refuses with "Autostart is not applicable in container mode", `GET /autostart` returns `"applicable": false` with the reason, and the Status card shows "Docker keeps Jig running (restart: unless-stopped). Make sure Docker Desktop starts when you log in." instead of the toggle. Do not turn on the host autostart for the same data, and do not run a host Jig on the same host port.

**Turning it off.** Use Docker, not Jig. `POST /power/stop` and `jig stop` refuse in container mode with this guidance, because the restart policy would start Jig again straight away:

```sh
docker compose stop jig        # Jig only; it stays stopped until you start it (also across reboots)
docker compose stop            # Jig, the sandboxes and any model profile you started (frees the GPU)
docker compose start           # start again
```

## Use Jig from your other devices (Tailscale)

The README's [Use Jig from your other devices](../README.md#use-jig-from-your-other-devices) explains pairing, revoking and the security design. In container mode, Jig cannot run `tailscale` or change the host's Tailscale settings, so `jig remote enable` and `disable` refuse and `GET /remote` returns `"applicable": false`. Set it up on the host instead:

1. Install Tailscale on the **host** and sign in, then turn on MagicDNS and HTTPS Certificates in the admin console.
2. Point `tailscale serve` at the port compose publishes, which is bound to `127.0.0.1` only:

   ```sh
   tailscale serve --bg --https=443 http://127.0.0.1:8766
   tailscale serve status                     # check; never use 'tailscale funnel' for this port
   ```

3. Tell Jig its tailnet name, which must end in `.ts.net`. Either set `JIG_REMOTE_HOSTNAME=machine.tailnet.ts.net` in `.env`, or set it in `deploy/jig.toml`:

   ```toml
   [remote]
   hostname = "machine.tailnet.ts.net"
   ```

   Then run `docker compose up -d` again. Jig only accepts this setting in container mode, and logs at start-up that it accepts requests for that name.

4. Pair each device from a browser **on the host** (`http://127.0.0.1:8766`, Settings > Add a device), or with `POST /devices/pairing`. Requests for the tailnet name can't create pairing codes.

**What is different from a host install.** Requests reach the container through Docker's port forwarding, so Jig cannot prove a request came from tailscaled, and it cannot trust the `Tailscale-User-Login` header. In container mode it therefore ignores Tailscale identity headers, so `[remote] allowed_logins` does not apply. A **paired device session is the only way in** over the tailnet name. Everything else is the same: the bearer token and token sign-in are refused for that name, cookies are `Secure`, `Origin` must be exactly `https://<hostname>`, and requests marked as funnelled are refused. Revoke devices in Settings, or rotate the token with `docker compose exec jig jig token rotate` to revoke them all. To turn remote access off, run `tailscale serve --https=443 off` on the host and remove the hostname setting.

A Tailscale sidecar container (the `tailscale/tailscale` image sharing Jig's network namespace, with its own auth key and serve config) also works in principle, but it is **not tested** with this compose file. The host `tailscale serve` above is the supported route.

## Updating

```sh
git pull                               # compose.yaml and deploy/jig.toml for the new version
docker compose pull                    # or: docker compose build
docker compose up -d                   # recreates only what changed; volumes are kept
```

Images are tagged with the full version (`0.1.0`), the minor version (`0.1`) and the commit (`sha-<commit>`). `compose.yaml` pins the version it was released with. Set `JIG_IMAGE` and `JIG_SANDBOX_IMAGE` in `.env` to pin another, and keep the two at the same version. Each release's images carry provenance and SBOM attestations; inspect them with `docker buildx imagetools inspect ghcr.io/rlesueur/jig:<version> --format '{{json .Provenance}}'`.

## Backups

The data volume holds everything except the vault key. Stop Jig so the SQLite database is consistent, then archive the volume with the Jig image itself:

```sh
docker compose stop jig
docker compose run --rm --no-deps -v "$PWD/backups:/backup" --entrypoint tar jig -czf /backup/jig-data.tgz -C /var/lib/jig/data .
docker compose start jig
```

(In PowerShell, use `"${PWD}\backups:/backup"`.) The workspace is backed up the same way, with `-C /var/lib/jig/sandbox/default` and another archive name. To restore into a fresh volume, run the same command with `-xzf /backup/jig-data.tgz -C /var/lib/jig/data` while Jig is stopped.

`docker compose down` keeps the volumes. `docker compose down -v` deletes them, including your memory, audit log and secrets.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `jig` keeps restarting; logs say `... was not ready within 600s` | The model server is not reachable at `JIG_MODEL_BASE_URL`, or is still loading. On Linux, check that it listens on `0.0.0.0` (see above) |
| `ModelCapabilityError` in the logs | The model failed the tool-call, JSON-schema or vision check. Pick a more capable model or fix the server's chat template (for example `--jinja` for llama.cpp) |
| `the ... sandbox service is not isolated: can reach the internet directly` | The `sandbox` network is not `internal: true`, or a sandbox service was put on another network |
| `the sandbox's /workspace is not Jig's workspace` | The `workspace` volume is not mounted at `/var/lib/jig/sandbox/<agent_id>` in Jig; change the mount if you changed `agent_id` |
| `VaultUnavailable: ...` | See [Vault key](#vault-key) |
| `InstanceLocked` | Another Jig uses the same data volume (for example a leftover `docker compose run`) |
