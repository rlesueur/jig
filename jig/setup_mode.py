"""Set-up mode: the web UI runs while the agent stays completely off.

Jig enters it when there is no model yet (an empty ``[model] base_url``, as the Windows installer ships) or
when the configured model fails the start-up checks: it can't be reached, it isn't loaded, its API key is
missing or refused, the cloud consent hasn't been given, it fails a capability check, or running code is on
but Docker isn't there. Nothing else is started: no agent, scheduler, tools or connectors. The person fixes
it in the web UI (the set-up page or Settings), Jig runs the same real checks on the new choice, and only
when they pass does it start the agent with it. There is no "run anyway".

Problems that set-up can't fix (another Jig already uses the data folder, the vault can't be opened, a
Tailscale Funnel points at the port) still stop Jig from starting, as before.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .audit import AuditLog
from .cloud import CloudConsentRequired, connection_summary, require_consent, resolve_api_key
from .config import Config
from .db import Database, now_iso
from .errors import (ModelCapabilityError, ModelError, ModelKeyMissing, ModelServerUnavailable,
                     SandboxUnavailable)
from .events import EventBus
from .friendly import Explained, explain
from .instance import InstanceLock
from .model import ModelClient
from .model_server import ModelServerSupervisor
from .vault import Vault
from .vision import VisionService

log = logging.getLogger(__name__)

# Start-up failures that set-up mode can fix from the web UI.
SETUP_ERRORS = (ModelServerUnavailable, ModelCapabilityError, ModelError, CloudConsentRequired, ModelKeyMissing,
                SandboxUnavailable)

NOT_SET_UP = Explained("Let's get Jig a model.", "Jig needs a model to think with. Choose one and Jig will check "
                       "it works before it starts.", "model")


class SetupState:
    """What the server keeps while the agent is off: the data folder's database, audit log and vault (for
    the API key and consent steps), and the problem to show. Shares the server's instance lock."""

    def __init__(self, config: Config, *, start_reason: str, problem: Explained, detail: str = "",
                 recheck: bool = False):
        self.config = config
        self.start_reason = start_reason
        self.problem = problem
        self.detail = detail
        self.recheck = recheck  # Jig tries the saved model again by itself every so often
        self.since = now_iso()
        self.bus = EventBus()
        self.db = Database(config.db_path)
        try:
            self.audit = AuditLog(self.db)
            self.vault = Vault(self.db, config.vault)
        except BaseException:
            self.db.close()
            raise
        self.model_server = ModelServerSupervisor(
            config.model_launch, config.data_dir / "logs", state_dir=config.data_dir,
            audit=lambda kind, summary, **data: self.audit.record(kind, summary, actor="runtime", **data))

    def as_dict(self) -> dict[str, Any]:
        return {"problem": self.problem.as_dict(), "detail": self.detail, "since": self.since, "recheck": self.recheck,
                "configured": bool(self.config.model.base_url), "settings_file": str(self.config.settings_file or ""),
                "config": str(self.config.source)}

    def connection(self) -> dict[str, Any]:
        return connection_summary(self.config, self.audit)

    def close(self) -> None:
        self.db.close()


def acquire_lock(config: Config, start_reason: str) -> InstanceLock:
    lock = InstanceLock(config.data_dir)
    lock.acquire(start_reason=start_reason, host=config.server.host, port=config.server.port,
                 config=str(config.source))
    return lock


async def check_model(config: Config, vault: Vault, audit: AuditLog,
                      progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The same checks Jig runs at start-up, against ``config``, without starting anything: consent for a
    cloud model, the API keys, that each model is served, then the capability probes. Returns what the
    probes found (for ``Jig.start(capabilities=...)``); raises the first failure."""
    from .runtime import probe_capabilities

    say = progress or (lambda step: None)
    say("consent")
    require_consent(config, audit)
    agent_key = resolve_api_key(config.model, vault, role="agent")
    sentinel_key = resolve_api_key(config.sentinel, vault, role="sentinel")
    model = ModelClient(config.model, label="agent model", api_key=agent_key)
    sentinel = ModelClient(config.sentinel, label="Sentinel model", api_key=sentinel_key)
    try:
        say("reach")
        info = {"agent": await model.health()}
        if config.sentinel.base_url.rstrip("/") != config.model.base_url.rstrip("/") or config.sentinel.name:
            info["sentinel"] = await sentinel.health()
        else:
            await sentinel.health()
        caps = await probe_capabilities(config, model, sentinel, VisionService(model, config.vision), say)
        return {"capabilities": caps, "models": info}
    finally:
        await model.aclose()
        await sentinel.aclose()
