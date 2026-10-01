"""Long-running sandbox service for ``[sandbox] backend = "compose"`` (Jig itself runs in a container).

Instead of ``docker exec``, Jig reaches the sandbox over the internal-only compose network. This
service runs as the unprivileged sandbox user and serves one JSON request line per connection:

* ``{"op": "selftest"}``: the service's own hardening (uid, capabilities, no-new-privileges, read-only
  root) and proof that it cannot reach the internet directly. Jig refuses to start unless it all holds.
* ``{"op": "exec", "argv": [...], "stdin": "<base64>", "timeout_s": N}`` (exec mode): runs the command
  in /workspace under ``timeout -s KILL`` and replies with the exit code and the tail of its output.
* ``{"op": "browser"}`` (browser mode): starts ``browser_server.py`` and joins the connection to its
  stdin and stdout, so Jig drives it with the same line protocol as over ``docker exec -i``. A new
  session replaces the previous one; closing the connection stops the browser.

* ``{"op": "ping"}``: liveness only, for the container health check.

Only the Jig container (``JIG_PEER``, resolved through Docker's DNS on every connection) may connect.
Loopback may only ``ping``. The process marks itself
non-dumpable, so code it runs cannot ptrace it or read its memory even though they share a uid.

Usage: sandbox_service.py exec|browser PORT       sandbox_service.py ping PORT (health check)
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import socket
import sys
from collections import deque

WORKSPACE = "/workspace"
OUTPUT_CAP = 256 * 1024
LINE_LIMIT = 64 * 1024 * 1024
PROBES = [("1.1.1.1", 443), ("8.8.8.8", 53)]


def _log(*parts: object) -> None:
    print("sandbox-service:", *parts, file=sys.stderr, flush=True)


def _status_field(name: str) -> str:
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].strip()
    return ""


def _root_read_only() -> bool:
    try:
        with open("/usr/.jig-write-probe", "w"):
            pass
    except OSError:
        return True
    os.unlink("/usr/.jig-write-probe")
    return False


def _direct_egress() -> dict:
    out: dict = {}
    for host, port in PROBES:
        try:
            socket.create_connection((host, port), 4).close()
            out[f"{host}:{port}"] = "connected"
        except OSError as exc:
            out[f"{host}:{port}"] = f"blocked ({type(exc).__name__}: {exc})"
    try:
        socket.getaddrinfo("example.com", 443)
        out["dns:example.com"] = "resolved"
    except OSError as exc:
        out["dns:example.com"] = f"blocked ({type(exc).__name__})"
    return out


def selftest(mode: str) -> dict:
    return {"ok": True, "service": mode, "uid": os.getuid(), "gid": os.getgid(),
            "cap_eff": _status_field("CapEff"), "cap_bnd": _status_field("CapBnd"),
            "no_new_privs": _status_field("NoNewPrivs"), "root_read_only": _root_read_only(),
            "workspace_writable": os.access(WORKSPACE, os.W_OK),
            "direct_egress": _direct_egress(), "proxy": os.environ.get("HTTPS_PROXY", "")}


def _allowed_peers() -> set[str]:
    peer = os.environ.get("JIG_PEER", "jig")
    try:
        return {info[4][0] for info in socket.getaddrinfo(peer, None, type=socket.SOCK_STREAM)}
    except OSError:
        return set()


async def _send(writer: asyncio.StreamWriter, reply: dict) -> None:
    writer.write((json.dumps(reply) + "\n").encode())
    await writer.drain()


async def _tail(stream: asyncio.StreamReader, keep: deque, total: list) -> None:
    size = 0
    while chunk := await stream.read(65536):
        size += len(chunk)
        keep.append(chunk)
        while sum(map(len, keep)) - len(keep[0]) >= OUTPUT_CAP:
            keep.popleft()
    total.append(size)


async def run_exec(req: dict) -> dict:
    argv = req.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return {"ok": False, "error": "argv must be a non-empty list of strings"}
    limit = int(req.get("timeout_s") or 60)
    stdin = base64.b64decode(req.get("stdin") or "")
    proc = await asyncio.create_subprocess_exec(
        "timeout", "-s", "KILL", str(limit), *argv, cwd=WORKSPACE,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err, sizes_out, sizes_err = deque(), deque(), [], []

    async def feed() -> None:
        try:
            proc.stdin.write(stdin)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    try:
        await asyncio.wait_for(asyncio.gather(feed(), _tail(proc.stdout, out, sizes_out),
                                              _tail(proc.stderr, err, sizes_err), proc.wait()), limit + 15)
    except TimeoutError:
        proc.kill()
        await proc.wait()
    b_out, b_err = b"".join(out)[-OUTPUT_CAP:], b"".join(err)[-OUTPUT_CAP:]
    return {"ok": True, "exit_code": proc.returncode, "stdout": base64.b64encode(b_out).decode(),
            "stderr": base64.b64encode(b_err).decode(),
            "stdout_bytes": sizes_out[0] if sizes_out else len(b_out),
            "stderr_bytes": sizes_err[0] if sizes_err else len(b_err)}


class BrowserHost:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.proc and self.proc.returncode is None:
            _log("a new browser session replaces the previous one")
            self.proc.kill()
            await self.proc.wait()
        proc = self.proc = await asyncio.create_subprocess_exec(
            "python3", "-u", "/opt/jig/browser_server.py", cwd=WORKSPACE, limit=LINE_LIMIT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=None)

        async def to_browser() -> None:
            while data := await reader.read(65536):
                proc.stdin.write(data)
                await proc.stdin.drain()

        async def from_browser() -> None:
            while data := await proc.stdout.read(65536):
                writer.write(data)
                await writer.drain()

        tasks = [asyncio.create_task(to_browser()), asyncio.create_task(from_browser())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (ConnectionError, OSError):
            pass
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            _log(f"browser session ended (exit {proc.returncode})")


async def main(mode: str, port: int) -> None:
    browser = BrowserHost()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")[0]
        try:
            line = await asyncio.wait_for(reader.readline(), 30)
            req = json.loads(line or b"{}")
            op = req.get("op")
            loopback = peer in ("127.0.0.1", "::1")
            if not (peer in _allowed_peers() or (loopback and op == "ping")):
                _log(f"refused {op!r} from {peer}: only Jig may use this service")
                await _send(writer, {"ok": False, "error": f"peer {peer} is not Jig"})
                return
            if op == "ping":
                await _send(writer, {"ok": True, "service": mode})
            elif op == "selftest":
                await _send(writer, await asyncio.to_thread(selftest, mode))
            elif op == "exec" and mode == "exec":
                await _send(writer, await run_exec(req))
            elif op == "browser" and mode == "browser":
                await browser.serve(reader, writer)
            else:
                await _send(writer, {"ok": False, "error": f"unknown operation {op!r} for the {mode} service"})
        except (ConnectionError, OSError, ValueError, TimeoutError) as exc:
            _log(f"request from {peer} failed: {type(exc).__name__}: {exc}")
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", port, limit=LINE_LIMIT)
    _log(f"{mode} service listening on :{port} (uid {os.getuid()}), serving {os.environ.get('JIG_PEER', 'jig')}")
    async with server:
        await server.serve_forever()


def ping(port: int) -> int:
    with socket.create_connection(("127.0.0.1", port), 10) as s:
        s.sendall(b'{"op": "ping"}\n')
        reply = json.loads(s.makefile("rb").readline())
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    command, port_arg = sys.argv[1], int(sys.argv[2])
    if command == "ping":
        raise SystemExit(ping(port_arg))
    if command not in ("exec", "browser"):
        raise SystemExit(f"unknown mode {command!r}; use exec, browser or ping")
    # PR_SET_DUMPABLE = 4: code run by this service (same uid) cannot ptrace it or read its memory.
    if ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) != 0:
        raise SystemExit("prctl(PR_SET_DUMPABLE, 0) failed")
    asyncio.run(main(command, port_arg))
