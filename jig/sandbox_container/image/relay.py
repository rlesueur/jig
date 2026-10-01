"""Egress relay. Runs in its own small container, the only thing on the sandbox's internal network
that can leave it. It forwards every TCP connection to Jig's egress proxy on the host and nowhere else,
so all outbound traffic from the sandbox is checked by the runtime.

Usage: python3 relay.py LISTEN_PORT TARGET_HOST TARGET_PORT
"""

import asyncio
import sys

LISTEN_PORT, TARGET_HOST, TARGET_PORT = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


async def _handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(TARGET_HOST, TARGET_PORT), 10)
    except (OSError, asyncio.TimeoutError) as exc:
        print(f"relay: cannot reach {TARGET_HOST}:{TARGET_PORT}: {exc!r}", file=sys.stderr, flush=True)
        client_w.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        client_w.close()
        return
    await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))
    for w in (up_w, client_w):
        w.close()


async def main() -> None:
    server = await asyncio.start_server(_handle, "0.0.0.0", LISTEN_PORT)
    print(f"relay listening on :{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    async with server:
        await server.serve_forever()


asyncio.run(main())
