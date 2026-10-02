"""Set-up mode in the server, and the routes the set-up page and Settings use (``/setup/...``).

``Controller`` owns the data folder's instance lock for the whole life of the server. At start-up it starts
the agent, or, if the model isn't set up or fails a check (``jig.setup_mode.SETUP_ERRORS``), keeps the
agent off and serves the web UI in set-up mode. ``apply()`` checks a new choice with the real start-up
checks and only when they pass saves it and starts (or restarts) the agent with it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import subprocess
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import settings_file
from ..cloud import (GIVEN, cloud_uses, consent_groups, consent_state, disclosure, key_secret_name, record_consent,
                     record_revocation, active_consents)
from ..code_execution import code_execution_status
from ..config import Config, load_config
from ..discovery import find_gpu, find_model_servers
from ..endpoints import PROVIDERS
from ..errors import ConfigError, ModelServerUnavailable, SandboxUnavailable, SecretNotFound
from ..friendly import Explained, explain
from ..instance import InstanceLock
from ..recommend import recommend
from ..runtime import Jig
from ..setup_mode import NOT_SET_UP, SETUP_ERRORS, SetupState, acquire_lock, check_model

log = logging.getLogger(__name__)

STEP_TEXT = {
    "consent": "Checking your choices",
    "reach": "Reaching the model",
    "tools": "Checking it can use tools",
    "structured": "Checking it can answer in Jig's format",
    "sentinel": "Checking the safety checker's model",
    "vision": "Checking it can see pictures",
    "saving": "Saving",
    "starting": "Starting Jig",
}

DOCKER_URL = "https://www.docker.com/products/docker-desktop/"
RECHECK_S = 15.0


class Controller:
    def __init__(self, app: Any, config: Config, start_reason: str):
        self.app = app
        self.base = config
        self.start_reason = start_reason
        self.lock: InstanceLock | None = None
        self._swap = asyncio.Lock()
        self._recheck: asyncio.Task | None = None
        app.state.jig = None
        app.state.setup = None

    # State ------------------------------------------------------------------------------------
    @property
    def jig(self) -> Jig | None:
        return self.app.state.jig

    @property
    def setup(self) -> SetupState | None:
        return self.app.state.setup

    @property
    def current(self) -> Jig | SetupState:
        """The running agent, or set-up mode's state; both have config, audit, vault, bus and model_server."""
        return self.jig or self.setup  # type: ignore[return-value]

    @property
    def config(self) -> Config:
        return self.current.config

    # Start and stop ---------------------------------------------------------------------------
    async def boot(self) -> None:
        self.lock = acquire_lock(self.base, self.start_reason)
        try:
            await self._start(self.base)
        except BaseException:
            self.lock.release()
            raise

    async def _start(self, config: Config, capabilities: dict[str, Any] | None = None) -> None:
        if not config.model.base_url:
            self._enter_setup(config, NOT_SET_UP)
            return
        try:
            jig = Jig(config, start_reason=self.start_reason, instance_lock=self.lock)
        except SETUP_ERRORS as exc:
            self._enter_setup(config, explain(exc, config), str(exc))
            return
        try:
            await jig.start(capabilities=capabilities)
        except SETUP_ERRORS as exc:
            self._enter_setup(config, explain(exc, config), str(exc),
                              recheck=isinstance(exc, ModelServerUnavailable))
            return
        self.app.state.jig = jig

    def _enter_setup(self, config: Config, problem: Explained, detail: str = "", *, recheck: bool = False) -> None:
        if detail:
            log.warning("set-up mode: the agent stays off until the model passes its checks: %s", detail)
        else:
            log.info("set-up mode: no model is set up yet; the agent stays off")
        state = SetupState(config, start_reason=self.start_reason, problem=problem, detail=detail, recheck=recheck)
        state.audit.record("setup.entered", f"set-up mode: {problem.title}", actor="runtime", problem=problem.as_dict(),
                           detail=detail[:2000])
        self.app.state.setup = state
        self.app.state.jig = None
        if recheck:
            self._recheck = asyncio.get_running_loop().create_task(self._recheck_loop(state), name="jig-setup-recheck")

    async def _recheck_loop(self, state: SetupState) -> None:
        """The saved model app couldn't be reached (still starting after the computer signed in, say): run
        the same checks on the same settings now and then, and start the agent once they pass. Stops when
        the person changes anything, or when the problem becomes one only they can fix."""
        while True:
            await asyncio.sleep(RECHECK_S)
            async with self._swap:
                if self.setup is not state:
                    return
                try:
                    result = await check_model(state.config, state.vault, state.audit)
                except ModelServerUnavailable as exc:
                    log.info("set-up mode: the model still can't be reached: %s", exc)
                    continue
                except (*SETUP_ERRORS, ConfigError) as exc:
                    log.warning("set-up mode: the model can be reached now, but: %s", exc)
                    state.problem, state.detail, state.recheck = explain(exc, state.config), str(exc), False
                    return
                log.info("set-up mode: the model passed its checks; starting the agent")
                await self._stop_current({"scope": "jig", "via": "setup-recheck"})
                await self._start(state.config, capabilities=result["capabilities"])
                return

    async def _stop_current(self, stop_request: dict[str, Any] | None) -> None:
        if self._recheck is not None and self._recheck is not asyncio.current_task():
            self._recheck.cancel()
        self._recheck = None
        if self.jig is not None:
            await self.jig.stop(stop_request=stop_request)
            self.app.state.jig = None
        if self.setup is not None:
            self.setup.close()
            self.app.state.setup = None

    async def shutdown(self, stop_request: dict[str, Any] | None) -> None:
        try:
            await self._stop_current(stop_request)
        finally:
            if self.lock is not None:
                self.lock.release()

    # Changing the model -----------------------------------------------------------------------
    def config_with(self, sections: dict[str, dict[str, Any]]) -> Config:
        """This install's config with ``sections`` applied over its saved settings, as it would load."""
        settings = {**settings_file.load(self.base.data_dir), **sections}
        config = load_config(self.base.source, data_dir=self.base.data_dir, sandbox_dir=self.base.sandbox_dir,
                             settings=settings)
        return dataclasses.replace(config, server=self.base.server)

    async def apply(self, sections: dict[str, dict[str, Any]], progress: Callable[[str], None]) -> dict[str, Any]:
        """Check ``sections`` for real; when every check passes, save them and (re)start the agent with them.
        A failed check changes nothing: the agent keeps running as it was, or set-up mode stays."""
        async with self._swap:
            holder = self.current
            config = self.config_with(sections)
            try:
                if config.sandbox.backend in ("container", "compose"):
                    status = await asyncio.to_thread(code_execution_status, config, set())
                    docker = status.get("docker") or {}
                    if not (docker.get("daemon") and docker.get("image_built")):
                        raise SandboxUnavailable("Docker isn't running, or Jig's sandbox image isn't built yet")
                result = await check_model(config, holder.vault, holder.audit, progress)
            except (*SETUP_ERRORS, ConfigError) as exc:
                raise CheckFailed(explain(exc, config), str(exc)) from exc
            progress("saving")
            if sections:
                settings_file.save(self.base.data_dir, sections)
                holder.audit.record("settings.saved", "model settings saved after passing the checks", actor="user",
                                    sections=sorted(sections), model=result["models"]["agent"].get("model"),
                                    base_url=config.model.base_url, via="web")
            progress("starting")
            await self._stop_current({"scope": "jig", "via": "settings"})
            await self._start(config, capabilities=result["capabilities"])
            if self.setup is not None:
                raise CheckFailed(self.setup.problem, self.setup.detail)
            return {"model": result["models"]["agent"], "capabilities": result["capabilities"]}

    async def turn_off_agent(self, problem: Explained, detail: str = "") -> None:
        """Stop the agent and go to set-up mode (for example after cloud consent is withdrawn)."""
        async with self._swap:
            config = self.config
            await self._stop_current({"scope": "jig", "via": "settings"})
            self._enter_setup(config, problem, detail)


class CheckFailed(Exception):
    """A choice failed a check (or the agent then failed to start with it), explained for the person."""

    def __init__(self, problem: Explained, detail: str):
        super().__init__(problem.text)
        self.problem, self.detail = problem, detail


# Routes ---------------------------------------------------------------------------------------

class ChoiceIn(BaseModel):
    kind: str
    app: str | None = None
    base_url: str | None = None
    name: str | None = None
    provider: str | None = None
    vision: bool = False
    # Cloud: the person read the disclosure and agreed to send their conversations to this provider.
    consent: bool = False


class KeyIn(BaseModel):
    provider: str
    key: str


class SandboxIn(BaseModel):
    enable: bool
    confirm: bool = False


def setup_router(controller: Controller, who: Callable[[Request], dict[str, Any]]) -> APIRouter:
    router = APIRouter(prefix="/setup")

    @router.get("")
    async def state() -> dict[str, Any]:
        cfg = controller.config
        saved = settings_file.load(controller.base.data_dir)
        out: dict[str, Any] = {
            "mode": "setup" if controller.setup else "running",
            "setup": controller.setup.as_dict() if controller.setup else None,
            "model": {"base_url": cfg.model.base_url, "name": (controller.jig.model.model_name if controller.jig
                                                               else cfg.model.name),
                      "provider": cfg.model.provider or None, "location": cfg.model.location.as_dict(),
                      "vision": cfg.vision.enabled},
            "saved_in_settings": sorted(saved),
            "config": str(cfg.source),
            "port": cfg.server.port,
            "sandbox": cfg.sandbox.backend,
        }
        return out

    @router.get("/discover")
    async def discover(url: str = "") -> dict[str, Any]:
        model = controller.config.model
        if not url and model.base_url and not model.location.is_cloud:
            url = model.base_url  # the model in use now, even on a port that isn't a usual one
        return await find_model_servers(url, skip_port=controller.base.server.port)

    @router.get("/gpu")
    async def gpu() -> dict[str, Any]:
        found = await asyncio.to_thread(find_gpu)
        return {"gpu": found, "recommendation": recommend(found.get("total_gb") if found["found"] else None)}

    @router.get("/providers")
    async def providers() -> list[dict[str, Any]]:
        vault = controller.current.vault
        stored = {s["name"] for s in vault.list()}
        return [{"id": p.id, "label": p.label, "key_url": p.key_url, "model": settings_file.cloud_default(p.id)["name"],
                 "key_stored": key_secret_name(p.id) in stored} for p in PROVIDERS.values()]

    @router.put("/cloud/key")
    async def set_key(request: Request, body: KeyIn) -> dict[str, Any]:
        if body.provider not in PROVIDERS:
            raise HTTPException(400, f"unknown provider {body.provider!r}")
        value = body.key.strip()
        if not value or any(c.isspace() for c in value):
            raise HTTPException(400, "That key is empty or has spaces in it. Copy it again and paste it here.")
        secret = key_secret_name(body.provider)
        holder = controller.current
        stored = holder.vault.set(secret, value, allowed_tools=[])
        holder.audit.record("vault.set", f"model API key {secret!r} stored", actor="user", secret=secret, via="web",
                            allowed_tools=[], **who(request))
        return {"stored": True, "secret": secret, "backend": stored["backend"]}

    @router.delete("/cloud/key/{provider}")
    async def delete_key(request: Request, provider: str) -> dict[str, Any]:
        secret = key_secret_name(provider)
        holder = controller.current
        in_use = (controller.config.model.api_key_secret, controller.config.sentinel.api_key_secret)
        if controller.jig and secret in in_use:
            raise HTTPException(409, "Jig is using this key now. Switch to another model first, then remove it.")
        try:
            holder.vault.delete(secret)
        except SecretNotFound:
            raise HTTPException(404, "There's no saved key for that provider.") from None
        holder.audit.record("vault.deleted", f"model API key {secret!r} deleted", actor="user", secret=secret,
                            via="web", **who(request))
        return {"deleted": True}

    @router.post("/cloud/disclosure")
    async def cloud_disclosure(body: ChoiceIn) -> dict[str, Any]:
        """Exactly what a cloud choice would send where, to show before asking for consent."""
        config = controller.config_with(settings_file.sections_for(body.model_dump()))
        uses = cloud_uses(config)
        given = all((s := consent_state(controller.current.audit, u.role, u.origin)) and s["kind"] == GIVEN
                    for u in uses)
        return {"cloud": bool(uses), "text": disclosure(uses) if uses else "", "already_given": bool(uses) and given}

    @router.post("/cloud/revoke")
    async def cloud_revoke(request: Request) -> dict[str, Any]:
        holder = controller.current
        active = active_consents(holder.audit)
        for endpoint_origin, roles in active.items():
            record_revocation(holder.audit, roles, endpoint_origin, via="web")
        using_cloud = bool(cloud_uses(controller.config))
        if using_cloud and controller.jig:
            await controller.turn_off_agent(
                Explained("Jig is off: you withdrew your OK for the cloud model.",
                          "Choose a model on this computer, or give your OK again, to turn Jig back on.", "consent"))
        return {"withdrawn": [o for o in active], "agent_stopped": using_cloud}

    def stream(work: Callable[[Callable[[str], None]], Any]) -> StreamingResponse:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def progress(step: str) -> None:
            queue.put_nowait({"step": step, "text": STEP_TEXT.get(step, step)})

        async def run() -> None:
            try:
                result = await work(progress)
                queue.put_nowait({"done": True, **(result or {})})
            except CheckFailed as exc:
                log.warning("set-up check failed: %s", exc.detail)
                queue.put_nowait({"error": exc.problem.as_dict(), "detail": exc.detail})
            except (*SETUP_ERRORS, ConfigError) as exc:
                log.warning("set-up check failed: %s", exc)
                queue.put_nowait({"error": explain(exc, controller.config).as_dict(), "detail": str(exc)})
            except Exception as exc:  # reported to the person, never swallowed
                log.exception("set-up check failed")
                queue.put_nowait({"error": explain(exc).as_dict(), "detail": f"{type(exc).__name__}: {exc}"})
            finally:
                queue.put_nowait(None)

        # Not tied to the response: closing the page mid-check doesn't leave the agent half switched.
        task = asyncio.get_running_loop().create_task(run())

        async def lines() -> AsyncIterator[str]:
            while (item := await queue.get()) is not None:
                yield json.dumps(item, default=str) + "\n"
            await task

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @router.post("/apply")
    async def apply(request: Request, body: ChoiceIn) -> StreamingResponse:
        try:
            sections = settings_file.sections_for(body.model_dump())
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc

        def consent(config: Config, holder: Any) -> None:
            uses = cloud_uses(config)
            missing = [u for u in uses if not ((s := consent_state(holder.audit, u.role, u.origin))
                                               and s["kind"] == GIVEN)]
            if missing and not body.consent:
                raise HTTPException(400, 'a cloud model needs your OK first: send "consent": true once the person '
                                         "has read the disclosure")
            for group in consent_groups(missing):
                record_consent(holder.audit, group, via="web")

        # Consent is checked before the stream starts, so a missing OK is a plain 400.
        consent(controller.config_with(sections), controller.current)
        return stream(lambda progress: controller.apply(sections, progress))

    @router.post("/retry")
    async def retry() -> StreamingResponse:
        """Run the checks again on the current settings (after starting the model app, say)."""
        return stream(lambda progress: controller.apply({}, progress))

    @router.get("/sandbox")
    async def sandbox_state() -> dict[str, Any]:
        cfg = controller.config
        names = {t.name for t in controller.jig.registry.all()} if controller.jig else set()
        status = await asyncio.to_thread(code_execution_status, cfg, names)
        docker = status.get("docker") or {}
        steps: list[str] = []
        if status["available"]:
            pass
        elif not docker.get("cli"):
            steps.append(f"Install Docker Desktop from {DOCKER_URL} and start it. It's free for personal use.")
        elif not docker.get("daemon"):
            steps.append("Start Docker Desktop, and wait until it says it's running.")
        if not status["available"] and not docker.get("image_built"):
            steps.append("Then build Jig's safe container here (a one-off download of about a gigabyte).")
        return {"on": status["available"], "backend": cfg.sandbox.backend, "docker": docker or None,
                "steps": steps, "can_build": bool(docker.get("daemon")) and not docker.get("image_built"),
                "can_turn_on": bool(docker.get("daemon")) and bool(docker.get("image_built"))
                and not status["available"]}

    @router.post("/sandbox/build")
    async def sandbox_build(request: Request) -> StreamingResponse:
        from ..sandbox_container.docker import IMAGE_DIR, docker_path, require_daemon

        cfg = controller.config
        log_path = cfg.data_dir / "logs" / "sandbox-build.log"

        async def work(progress: Callable[[str], None]) -> dict[str, Any]:
            await asyncio.to_thread(require_daemon)
            progress("building")
            log_path.parent.mkdir(parents=True, exist_ok=True)

            def build() -> int:
                with log_path.open("w", encoding="utf-8") as fh:
                    return subprocess.run([docker_path(), "build", "-t", cfg.sandbox.image, str(IMAGE_DIR)],
                                          stdout=fh, stderr=subprocess.STDOUT,
                                          **({"creationflags": subprocess.CREATE_NO_WINDOW}
                                             if hasattr(subprocess, "CREATE_NO_WINDOW") else {})).returncode

            code = await asyncio.to_thread(build)
            if code != 0:
                raise SandboxUnavailable(f"docker build of {cfg.sandbox.image} failed (exit {code}); see {log_path}")
            controller.current.audit.record("sandbox.image_built", f"built {cfg.sandbox.image}", actor="user",
                                            via="web", **who(request))
            return {"built": cfg.sandbox.image}

        return stream(work)

    @router.post("/sandbox")
    async def sandbox_set(body: SandboxIn) -> StreamingResponse:
        if body.enable and body.confirm is not True:
            raise HTTPException(400, 'running code: send "confirm": true once the person has agreed')
        sections = {"sandbox": {"backend": "container" if body.enable else "directory"}}
        if not controller.config.model.base_url:
            raise HTTPException(409, "Set up a model first; then you can turn on running code.")
        return stream(lambda progress: controller.apply(sections, progress))

    return router
