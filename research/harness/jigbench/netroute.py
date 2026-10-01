"""Route a fixed set of reserved test host names to local ports, and refuse every other host.

This is isolation tooling for benchmark runs, not attack content. Jig's core rule `no-local-network`
(correctly) refuses any outbound URL that resolves to a loopback or private address. To let a published
benchmark's own tool environment be exercised against local fixtures without ever touching a third party,
the harness:

1. gives Jig an HTTP client whose transport maps a fixed set of host names under the reserved `.test`
   TLD (RFC 2606) to 127.0.0.1:<port>, and **refuses every other host** (so a benchmark run can never
   reach the real internet); and
2. exempts exactly those host names from the `no-local-network` address check, delegating every other
   URL to Jig's real check unchanged.

Everything else in Jig (the gate, core and custom rules, the Sentinel, approvals, the tools) runs
unmodified. In the current scope `services.routes()` returns no hosts, so the transport simply blocks all
outbound access during a run. A native "benchmark hosts" hook is recommended to the core team so this
in-process exemption is no longer needed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx

BENCH_SUFFIX = ".jigbench.test"


class BenchmarkRoutingError(httpx.ConnectError):
    pass


class BenchTransport(httpx.AsyncBaseTransport):
    def __init__(self, routes: dict[str, int]):
        for host in routes:
            if not host.endswith(BENCH_SUFFIX):
                raise ValueError(f"benchmark host {host!r} must end with {BENCH_SUFFIX}")
        self.routes = dict(routes)
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host not in self.routes:
            raise BenchmarkRoutingError(f"jigbench: {host!r} is not a benchmark host; real internet access is "
                                        "disabled during benchmark runs", request=request)
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.routes[host])
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def bench_client(routes: dict[str, int]) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": "Jig/0.1 (+local personal agent)"}, trust_env=False,
                             transport=BenchTransport(routes))


@contextmanager
def benchmark_hosts_exempt(routes: dict[str, int]) -> Iterator[None]:
    """Exempt exactly the benchmark host names from Jig's no-local-network address check."""
    import jig.policy.core as core
    import jig.tools.web as web

    original = web.public_address_problem
    if getattr(original, "_jigbench_patched", False):
        raise RuntimeError("benchmark host exemption is already installed")
    allowed = frozenset(routes)

    async def checked(url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and parts.hostname in allowed:
            return None
        return await original(url)

    checked._jigbench_patched = True  # type: ignore[attr-defined]
    web.public_address_problem = checked
    core.public_address_problem = checked
    try:
        yield
    finally:
        web.public_address_problem = original
        core.public_address_problem = original


def install(jig: Any, routes: dict[str, int]) -> httpx.AsyncClient:
    """Swap the Jig instance's tool HTTP client for the benchmark client; returns the old one to close."""
    old = jig.http
    jig.http = bench_client(routes)
    return old
