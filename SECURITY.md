# Security policy

Jig is an agent that can read the web, run tools and hold credentials, so security reports are very welcome. Jig is under active development and has not been independently audited.

## Reporting a vulnerability

Please **do not open a public issue** for a vulnerability.

Report it privately through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability** (<https://github.com/rlesueur/jig/security/advisories/new>). Include:

- what you found and why it matters (for example, a way to bypass the gate, reach the local network, read a vault value or alter the audit log);
- the Jig commit or version, your operating system, the sandbox backend (`directory` or `container`) and the model server you used;
- the steps, prompt, page content or configuration needed to reproduce it.

You should receive an acknowledgement within 7 days. Fixes are developed in a private advisory and published with credit to the reporter, unless you would rather stay anonymous.

## Supported versions

Jig has no tagged releases yet. Security fixes go into the `main` branch.

## What the safety model does

Every tool call passes through one gate, enforced in code: schema validation, the research/action mode, hard-coded core rules, your custom rules, an isolated Sentinel review of outbound and side-effecting actions, human approvals, and vault references resolved only when the tool runs. The [README](README.md#safety-model) describes each step.

## Known limits

These are known and documented; reports that show they are worse than described are still useful.

- **The model is not trusted, but it is not fully contained either.** The gate decides what a tool call may do. It cannot stop a model from giving you a wrong or misleading answer in plain text.
- **The Sentinel is a model.** It reduces the risk of prompt injection but, like any model-based reviewer, it can be wrong. Core rules and approvals do not depend on it.
- **The `directory` sandbox is a folder jail, not an operating-system sandbox.** It rejects path traversal, absolute paths, alternate data streams, reserved names and links that lead out, but it runs in the Jig process with your user's permissions. Use the `container` backend for code execution and browsing.
- **The `container` backend relies on Docker.** Its isolation is only as strong as your Docker installation and kernel. Keep Docker up to date.
- **The local API binds to `127.0.0.1`.** It is designed for local clients only; do not expose it to a network or put it behind a public reverse proxy.
- **Local files are only as private as your machine.** The SQLite database, audit log and memory are stored unencrypted in `data/`. Vault values are protected with Windows DPAPI or the system keyring, so they are tied to your user account.
- **The model server is outside Jig's control.** Jig sends prompts, tool results and (with vision on) images to the endpoint you configure. Point it only at a server you trust, ideally on the same machine.
