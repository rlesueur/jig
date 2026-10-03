"""HTTP API: chat, goals, tasks, schedules, approvals, rules, memory, notes, audit, vault and events.

Every route needs the API token (see ``jig.auth``) except ``/health``, the web
UI's static files and the browser session exchange.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .. import __version__
from ..auth import (Auth, AuthMiddleware, Principal, TokenStore, clear_cookie_headers, device_cookie_header,
                    session_cookie_header)
from ..autostart.api import autostart_router
from ..code_execution import code_execution_status
from ..config import MODEL_KEY_PREFIX, Config
from ..connectors import SECRET_PREFIX as CONNECTOR_SECRET_PREFIX
from ..connectors import connect as connect_account
from ..connectors import google as google_connector
from ..connectors import microsoft as microsoft_connector
from ..connectors import provider as connector_provider
from ..connectors.guide import guide as connector_guide
from ..connectors import signal_setup
from ..connectors import walkthrough as connector_walkthrough
from ..constants import EventType, Mode
from ..db import now_iso
from ..devices import DeviceStore, PairingError
from ..errors import CannotDelete, ConfigError, ConnectorError, JigError
from ..errors import ModelServerUnavailable, NotFound, SecretNotFound, ToolArgumentError
from ..events import SubscriberOverflow
from ..instance import take_stop_request
from ..model_server import ModelServerNotManaged
from ..policy.approvals import ApprovalConflict
from ..policy.core import CORE_RULES
from ..power import PowerRefused, autostart_summary, check_stop, gpu_usage, power_state, start_again
from ..remote import Refused, RemoteAccess, RemoteError, RequestSource
from ..friendly import explain
from ..runtime import Jig
from .setup import Controller, setup_router, timezone_state


class ChatIn(BaseModel):
    message: str = ""
    session_id: str | None = None
    mode: Mode = Mode.ACTION
    # After a reply Jig stopped for repeating itself: "retry" asks again, "continue" carries it on.
    action: Literal["send", "retry", "continue"] = "send"


class TaskRetryIn(BaseModel):
    # Carry the stopped task on from where it stopped, instead of running it again from the start.
    continue_anyway: bool = False


class GoalIn(BaseModel):
    description: str = Field(min_length=1)
    title: str | None = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    mode: Mode = Mode.ACTION
    delay_s: float = 0.0


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    mode: Mode = Mode.RESEARCH
    # Either interval_s (every so many seconds) or repeat (see jig.recurrence: interval, daily, weekdays,
    # weekly or cron). Calendar times are in timezone, which defaults to [runtime] timezone.
    interval_s: float | None = None
    repeat: dict[str, Any] | None = None
    timezone: str | None = None
    start_in_s: float = 0.0


class SchedulePatch(BaseModel):
    name: str | None = Field(None, min_length=1)
    prompt: str | None = Field(None, min_length=1)
    mode: Mode | None = None
    enabled: bool | None = None
    interval_s: float | None = None
    repeat: dict[str, Any] | None = None
    timezone: str | None = None


class ApprovalIn(BaseModel):
    approve: bool
    note: str | None = None


class RuleIn(BaseModel):
    tool: str = Field(min_length=1)
    decision: Literal["allow", "ask", "block"]
    arg: str | None = None
    pattern: str | None = None
    priority: int = 0
    note: str | None = None
    enabled: bool = True


class RulePatch(BaseModel):
    tool: str | None = None
    decision: Literal["allow", "ask", "block"] | None = None
    arg: str | None = None
    pattern: str | None = None
    priority: int | None = None
    note: str | None = None
    enabled: bool | None = None


class MemoryIn(BaseModel):
    content: str = Field(min_length=1)
    kind: str = "fact"
    tags: list[str] = []


class MemorySearch(BaseModel):
    q: str = Field(min_length=1)
    limit: int = Field(100, ge=1, le=1000)


class MemoryPatch(BaseModel):
    content: str | None = None
    kind: str | None = None
    tags: list[str] | None = None


class NotePatch(BaseModel):
    title: str | None = Field(None, min_length=1)
    body: str | None = Field(None, min_length=1)


class SecretIn(BaseModel):
    value: str = Field(min_length=1)
    allowed_tools: list[str] = []


class SessionIn(BaseModel):
    token: str | None = None
    code: str | None = None


class ConfirmIn(BaseModel):
    # Required, and must be true: the caller confirms that the user has agreed to this.
    confirm: bool | None = None


class PowerStopIn(ConfirmIn):
    scope: str


class MemoryWipeIn(ConfirmIn):
    # Also delete every note (Settings > Forget everything does).
    notes: bool = False


class ConnectIn(ConfirmIn):
    access: str | None = None
    method: str | None = None  # one of the provider's methods: "oauth", "device" or "token"
    # What a token sign-in asks for. Typed Any and checked by hand, so a malformed value is never echoed
    # back in a validation error.
    values: Any = None


class WalkthroughCheckIn(ConfirmIn):
    # The non-secret value a step checks (an application ID, a homeserver, a channel). Checked by hand.
    values: Any = None


class SignalLinkIn(ConfirmIn):
    signal_cli: str = Field(min_length=1, max_length=500)


class ClientIn(ConfirmIn):
    # Google: the text of the client file downloaded from the Google Cloud console. Microsoft: your own app
    # registration's client ID (and tenant). Typed Any for the same reason as ConnectIn.values.
    client_json: Any = None
    client_id: Any = None
    tenant: Any = None


class PairingIn(BaseModel):
    # Days until the new device's session expires; leave out for no expiry (it can always be revoked).
    expires_in_days: int | None = None


class PairIn(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)


def _set(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True)


def _source(request: Request) -> RequestSource:
    return request.scope["state"]["source"]


def _who(request: Request) -> dict[str, Any]:
    """Who asked, for the audit log: how they signed in, from where, and which paired device."""
    principal: Principal | None = request.scope["state"].get("principal")
    source = _source(request)
    who: dict[str, Any] = {"auth_via": principal.via if principal else None, "source": source.kind}
    if source.login:
        who["tailscale_login"] = source.login
    if principal and principal.device:
        who["by_device_id"], who["by_device_name"] = principal.device["id"], principal.device["name"]
    return who


def _require_local(request: Request, what: str) -> None:
    if _source(request).kind != "local":
        raise HTTPException(403, f"{what} only works on the host computer itself, not over the tailnet")


def _require_confirm(body: ConfirmIn, what: str) -> None:
    if body.confirm is not True:
        raise HTTPException(400, f'{what}: send "confirm": true once the user has confirmed')


def _qr_data_uri(text: str) -> str:
    import segno  # pure Python, generated locally: the pairing URL is never sent to a QR service

    return segno.make(text, error="m").svg_data_uri(scale=6, border=4, light="#fff")


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AVATAR_JS = Path(__file__).resolve().parents[2] / "avatar" / "jig-avatar.js"
# The Windows installer writes this next to the jig package, and registers jig:// links that start Jig's
# tray app (so the "Jig is off" page can offer a Turn on button).
DESKTOP_MARKER = Path(__file__).resolve().parents[2] / "installed.json"
DESKTOP = {"installed": DESKTOP_MARKER.is_file(), "start_link": "jig://start" if DESKTOP_MARKER.is_file() else None}

# The UI is dependency-free and same-origin only. The avatar's shadow DOM uses an inline <style>.
UI_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


class UIStatic(StaticFiles):
    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update(UI_HEADERS)
        return response


def create_app(config: Config, *, start_reason: str = "manual") -> FastAPI:
    for required in (WEB_DIR / "index.html", AVATAR_JS):
        if not required.is_file():
            raise ConfigError(f"the web UI needs {required}, which is missing")
    tokens = TokenStore(config.data_dir)
    tokens.ensure()  # created on first run; fails loudly if it cannot be made private
    devices = DeviceStore(config.data_dir, tokens.get)
    auth = Auth(tokens, devices)
    remote = RemoteAccess(config)
    port = config.server.port

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Refuses to start (RemoteError) if a Tailscale Funnel leads to this port.
        remote_records = await asyncio.to_thread(remote.startup_check, port)
        # Starts the agent, or set-up mode if the model isn't set up or fails its checks (jig.setup_mode).
        try:
            await controller.boot()
        except JigError as exc:
            logging.getLogger("jig.startup").error("Jig didn't start. %s", explain(exc, config))
            raise
        for kind, summary, data in remote_records:
            controller.current.audit.record(kind, summary, actor="runtime", **data)
        on_started = getattr(app.state, "on_started", None)
        if on_started:
            on_started(controller)
        try:
            yield
        finally:
            for job in getattr(app.state, "signal_jobs", ()):
                await job.close()  # ends a signal-cli link still waiting for the phone
            stop_request = getattr(app.state, "stop_request", None) or take_stop_request(config.data_dir)
            await controller.shutdown(stop_request)
            devices.close()

    app = FastAPI(title="Jig", version=__version__, lifespan=lifespan)
    app.state.auth = auth
    app.state.devices = devices
    app.state.remote = remote
    app.state.stop_request = None
    app.state.closing = asyncio.Event()  # set by run_server as shutdown begins; ends live event streams
    app.add_middleware(AuthMiddleware, auth=auth, remote=remote)
    if config.sandbox.backend == "compose":
        from ..sandbox_compose import SandboxPeerGuard

        app.add_middleware(SandboxPeerGuard)  # added last, so it runs before authentication

    controller = Controller(app, config, start_reason)
    app.state.controller = controller

    def J(request: Request) -> Jig:
        jig = request.app.state.jig
        if jig is None:
            raise HTTPException(503, "Jig is off until its model is set up and passes its checks. Open Jig's set-up "
                                     "page to choose one.")
        return jig

    def A(request: Request) -> Any:
        """The audit log, in set-up mode too."""
        return controller.current.audit

    app.include_router(autostart_router(config))
    app.include_router(setup_router(controller, _who))

    # Web UI (public static files; every API call it makes is authenticated) ------------------
    @app.get("/", include_in_schema=False)
    async def ui_index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", headers=UI_HEADERS)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml", headers=UI_HEADERS)

    @app.get("/avatar/jig-avatar.js", include_in_schema=False)
    async def avatar_js() -> FileResponse:
        return FileResponse(AVATAR_JS, media_type="text/javascript", headers=UI_HEADERS)

    app.mount("/web", UIStatic(directory=WEB_DIR), name="web")

    # Authentication ---------------------------------------------------------------------------
    @app.post("/auth/session")
    async def create_session(request: Request, body: SessionIn) -> JSONResponse:
        """Exchange the API token or a one-time login code for an HttpOnly session cookie."""
        source = _source(request)
        if source.kind != "local":
            raise HTTPException(403, "Over the tailnet, pair this device instead of signing in with the token or a "
                                     "login code: on the host, open Settings > Use Jig from your other devices > "
                                     "Add a device.")
        if body.code:
            ok = auth.redeem_login_code(body.code)
        elif body.token:
            ok = auth.token_valid(body.token.strip())
        else:
            raise HTTPException(400, "send either 'token' or 'code'")
        if not ok:
            raise HTTPException(401, "that token or login code is not valid (login codes work once and expire)")
        value, max_age = auth.new_session()
        return JSONResponse({"authenticated": True, "expires_in": max_age},
                            headers={"Set-Cookie": session_cookie_header(value, max_age, secure=source.secure)})

    @app.get("/auth/session")
    async def get_session(request: Request) -> dict[str, Any]:
        source = _source(request)
        try:
            principal = auth.authenticate({k.lower(): v for k, v in request.headers.items()}, source)
        except Refused as exc:
            return {"authenticated": False, "via": None, "source": source.kind, "reason": exc.message}
        out: dict[str, Any] = {"authenticated": principal is not None, "via": principal.via if principal else None,
                               "source": source.kind}
        if principal and principal.device:
            out["device"] = {"id": principal.device["id"], "name": principal.device["name"]}
        return out

    @app.post("/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        """Sign this browser out. On a paired device, this also unpairs it (revokes its device session)."""
        principal: Principal | None = request.scope["state"].get("principal")
        if principal and principal.device:
            devices.revoke(principal.device["id"], "signed out on the device")
            A(request).record("device.revoked", f"device {principal.device['name']!r} signed out",
                                               actor="user", device_id=principal.device["id"], **_who(request))
        response = JSONResponse({"authenticated": False})
        for header in clear_cookie_headers(secure=_source(request).secure):
            response.headers.append("Set-Cookie", header)
        return response

    @app.post("/auth/login-code")
    async def login_code(request: Request) -> dict[str, Any]:
        """A one-time code for signing a browser in (used by 'jig ui'); it never carries the token itself."""
        _require_local(request, "Creating a sign-in code")
        code, ttl = auth.new_login_code()
        return {"code": code, "expires_in": ttl}

    @app.post("/auth/pair")
    async def pair_device(request: Request, body: PairIn) -> JSONResponse:
        """Public: a new device swaps a pairing code (from 'Add a device' on the host) for its own device session."""
        source = _source(request)
        if request.headers.get("origin") != source.origin:
            raise HTTPException(403, "cross-origin request refused")
        try:
            device, value = devices.redeem(body.code, name=body.name, paired_via=source.kind,
                                           tailscale_login=source.login)
        except PairingError as exc:
            A(request).record("device.pairing_failed", str(exc), actor="user", **_who(request))
            raise HTTPException(401, str(exc)) from exc
        A(request).record("device.paired", f"device {device['name']!r} paired", actor="user",
                                           device_id=device["id"], device_name=device["name"],
                                           expires_at=device["expires_at"], **_who(request))
        cookie = device_cookie_header(value, devices.cookie_max_age(device), secure=source.secure)
        return JSONResponse({"paired": True, "device": device}, headers={"Set-Cookie": cookie})

    @app.post("/auth/token/rotate")
    async def rotate_token(request: Request, body: ConfirmIn) -> JSONResponse:
        """Replace the master token. Signs out every browser and revokes every paired device."""
        _require_local(request, "Rotating the master token")
        _require_confirm(body, "The token was not rotated")
        tokens.rotate()
        revoked = devices.revoke_all("the master API token was rotated")
        A(request).record("auth.token_rotated", f"master token rotated; {revoked} device(s) revoked",
                                           actor="user", devices_revoked=revoked, **_who(request))
        response = JSONResponse({"rotated": True, "devices_revoked": revoked,
                                 "message": "The master token was replaced. Every browser is signed out and every "
                                            "paired device is revoked. Sign in again with 'jig ui'."})
        for header in clear_cookie_headers(secure=_source(request).secure):
            response.headers.append("Set-Cookie", header)
        return response

    @app.exception_handler(NotFound)
    @app.exception_handler(SecretNotFound)
    async def not_found(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    @app.exception_handler(ToolArgumentError)
    async def bad_request(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Only where and what went wrong: FastAPI's default also echoes the submitted value, which may be a
        # secret someone typed.
        problems = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', 'invalid')}" for e in exc.errors()]
        return JSONResponse({"error": "; ".join(problems) or "invalid request"}, status_code=422)

    @app.exception_handler(ApprovalConflict)
    @app.exception_handler(CannotDelete)
    async def conflict(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ModelServerUnavailable)
    async def model_down(request: Request, exc: Exception) -> JSONResponse:
        jig = getattr(request.app.state, "jig", None)
        # Where the model is (local or cloud), so the UI can say what to check.
        return JSONResponse({"error": str(exc), **({"connection": jig.connection()} if jig else {})}, status_code=503)

    # Health and state ------------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Public liveness check. It says only that the server is up: no versions, models or paths."""
        return {"status": "ok"}

    @app.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        if controller.setup is not None:
            st = controller.setup
            return {"status": "setup", "version": __version__, "setup": st.as_dict(), "connection": st.connection(),
                    "model_endpoint": st.config.model.base_url, "start_reason": st.start_reason,
                    "vault_backend": st.vault.backend, "desktop": DESKTOP, "timezone": timezone_state(st.config)}
        jig = J(request)
        model = await jig.model.health()
        sentinel = await jig.sentinel_model.health()
        return {
            "status": "ok",
            "version": __version__,
            "desktop": DESKTOP,
            "timezone": timezone_state(jig.config),
            "model_endpoint": jig.config.model.base_url,
            "sentinel_endpoint": jig.config.sentinel.base_url,
            # Local or cloud, for the agent and for the safety checker (and what a cloud endpoint receives).
            "connection": jig.connection(),
            "agent": jig.agent_status(),
            "model": model,
            "sentinel_model": sentinel,
            "capabilities": jig.capabilities,
            "vault_backend": jig.vault.backend,
            "sandbox": str(jig.sandbox.root),
            "scheduler": {"last_tick": jig.scheduler.last_tick, "running": jig.scheduler.running_task_ids,
                          "max_concurrent": jig.scheduler.max_concurrent},
            "avatar": jig.tracker.current,
            "start_reason": jig.start_reason,
        }

    @app.get("/state")
    async def state(request: Request) -> dict[str, Any]:
        return {**J(request).tracker.current, "start_reason": J(request).start_reason}

    @app.get("/tools")
    async def tools(request: Request) -> list[dict[str, Any]]:
        return [t.describe() for t in J(request).registry.all()]

    @app.get("/sandbox")
    async def sandbox_status(request: Request) -> dict[str, Any]:
        """Whether Jig can run code; if not, whether Docker is there and what to do to turn it on."""
        names = {t.name for t in J(request).registry.all()}
        return await asyncio.to_thread(code_execution_status, J(request).config, names)

    # Events ----------------------------------------------------------------
    async def until_gone(ws: WebSocket) -> None:
        """Return once the socket has closed: the page went, or Jig is turning off (uvicorn then sends the page
        1012 and delivers a disconnect here). Without this an idle stream holds shutdown open until uvicorn's
        graceful timeout. The page sends nothing on this socket."""
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/events")
    async def events_ws(ws: WebSocket) -> None:
        jig: Jig | None = ws.app.state.jig
        await ws.accept()
        gone = asyncio.create_task(until_gone(ws))
        try:
            if jig is None:
                # Set-up mode: nothing happens until the agent starts; then the page reconnects to its events.
                await ws.send_json({"type": "setup", "snapshot": True, "data": {}})
                while ws.app.state.jig is None:
                    if (await asyncio.wait({gone}, timeout=1))[0]:
                        return
                await ws.close(code=1012, reason="Jig started")
                return
            sub = jig.bus.subscribe()
            try:
                await ws.send_json({"type": EventType.AVATAR_STATE.value, "snapshot": True,
                                    "data": jig.tracker.current})
                while True:
                    getter = asyncio.ensure_future(sub.get())
                    await asyncio.wait({getter, gone}, timeout=2, return_when=asyncio.FIRST_COMPLETED)
                    if gone.done():
                        getter.cancel()
                        return
                    if not getter.done():
                        getter.cancel()
                        if ws.app.state.jig is not jig:  # the model was changed: this runtime has stopped
                            await ws.close(code=1012, reason="Jig restarted")
                            return
                        continue
                    await ws.send_json(getter.result().as_dict())
            except SubscriberOverflow as exc:
                await ws.close(code=1013, reason=str(exc))
            finally:
                sub.close()
        except WebSocketDisconnect:
            pass
        finally:
            gone.cancel()

    @app.get("/events/sse")
    async def events_sse(request: Request) -> StreamingResponse:
        jig = J(request)
        sub = jig.bus.subscribe()

        async def gen() -> AsyncIterator[str]:
            try:
                snapshot = {"type": EventType.AVATAR_STATE.value, "snapshot": True, "data": jig.tracker.current}
                yield f"data: {json.dumps(snapshot)}\n\n"
                closing = asyncio.ensure_future(request.app.state.closing.wait())
                try:
                    while not await request.is_disconnected():
                        getter = asyncio.ensure_future(sub.get())
                        await asyncio.wait({getter, closing}, timeout=15, return_when=asyncio.FIRST_COMPLETED)
                        if closing.done():  # Jig is turning off: end the stream rather than hold shutdown open
                            getter.cancel()
                            return
                        if not getter.done():
                            getter.cancel()
                            yield ": keep-alive\n\n"
                            continue
                        event = getter.result()
                        yield f"event: {event.type}\ndata: {json.dumps(event.as_dict(), default=str)}\n\n"
                finally:
                    closing.cancel()
            except SubscriberOverflow as exc:
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            finally:
                sub.close()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/events/recent")
    async def events_recent(request: Request, after: int = 0, type: str | None = None) -> list[dict[str, Any]]:
        return [e.as_dict() for e in J(request).bus.recent if e.seq > after and (type is None or e.type == type)]

    # Chat ------------------------------------------------------------------
    @app.post("/chat")
    async def chat(request: Request, body: ChatIn) -> StreamingResponse:
        jig = J(request)
        stream = jig.chat(body.message, session_id=body.session_id, mode=body.mode, action=body.action)
        first = await anext(stream)

        async def gen() -> AsyncIterator[str]:
            yield json.dumps(first) + "\n"
            async for item in stream:
                yield json.dumps(item, default=str) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.get("/sessions/{session_id}")
    async def session(request: Request, session_id: str) -> dict[str, Any]:
        return {"id": session_id, "messages": J(request).store.get_session(session_id)}

    # Deleting conversations and job results: the audit entries have ids and counts, never content -----------
    @app.get("/sessions")
    async def list_sessions(request: Request, limit: int = Query(100, le=1000)) -> list[dict[str, Any]]:
        return J(request).store.list_conversations(limit=limit)

    @app.post("/sessions/wipe")
    async def wipe_sessions(request: Request, body: ConfirmIn) -> dict[str, Any]:
        _require_confirm(body, "No conversation was deleted")
        jig = J(request)
        out = jig.store.wipe_conversations()
        jig.audit.record("conversation.wiped", f"all conversations deleted ({out['conversations']})", actor="user",
                         **out, **_who(request))
        return out

    @app.get("/sessions/{session_id}/transcript")
    async def session_transcript(request: Request, session_id: str) -> dict[str, Any]:
        """What you and Jig said, without the tool calls in between."""
        return J(request).store.get_conversation(session_id, with_messages=True)

    @app.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(request: Request, session_id: str) -> None:
        jig = J(request)
        out = jig.store.delete_conversation(session_id)
        jig.audit.record("conversation.deleted", f"conversation {session_id} deleted", actor="user",
                         session_id=session_id, **out, **_who(request))

    @app.delete("/tasks/{task_id}", status_code=204)
    async def delete_task(request: Request, task_id: str) -> None:
        jig = J(request)
        out = jig.delete_task(task_id)
        jig.audit.record("task.deleted", f"task {task_id} and its results deleted", actor="user", task_id=task_id,
                         **out, **_who(request))

    @app.delete("/goals/{goal_id}", status_code=204)
    async def delete_goal(request: Request, goal_id: str) -> None:
        jig = J(request)
        out = jig.delete_goal(goal_id)
        jig.audit.record("goal.deleted", f"goal {goal_id}, its tasks and their results deleted", actor="user",
                         goal_id=goal_id, **out, **_who(request))

    @app.post("/jobs/wipe")
    async def wipe_jobs(request: Request, body: ConfirmIn) -> dict[str, Any]:
        """Delete every finished goal and task with its results; unfinished ones are kept (``kept_unfinished``)."""
        _require_confirm(body, "No job was deleted")
        jig = J(request)
        out = jig.wipe_jobs()
        jig.audit.record("job.wiped", f"all finished jobs deleted ({out['goals']} goals, {out['tasks']} tasks)",
                         actor="user", **out, **_who(request))
        return out

    @app.post("/forget")
    async def forget_everything(request: Request, body: ConfirmIn) -> dict[str, Any]:
        """Settings > Forget everything: every memory and note, every conversation and every finished job with its
        results. Then the database file is rebuilt, so no deleted text is left in its free space."""
        _require_confirm(body, "Nothing was deleted")
        jig = J(request)
        jobs = jig.wipe_jobs(vacuum=False)  # first: it is the one that can refuse (a task still stopping)
        conversations = jig.store.wipe_conversations(vacuum=False)
        memories = jig.memory.wipe()
        notes = jig.store.wipe_notes()
        out = {"memories": memories["forgotten"], "notes": notes["deleted"],
               "conversations": conversations["conversations"], "goals": jobs["goals"], "tasks": jobs["tasks"],
               "kept_replying": conversations["kept_replying"], "kept_unfinished": jobs["kept_unfinished"],
               "wal_cleared": jig.db.vacuum()}
        jig.audit.record("everything.forgotten", "memories, notes, conversations and finished jobs deleted",
                         actor="user", **out, **_who(request))
        return out

    # Goals -----------------------------------------------------------------
    @app.post("/goals", status_code=201)
    async def create_goal(request: Request, body: GoalIn) -> dict[str, Any]:
        return J(request).create_goal(description=body.description, title=body.title)

    @app.get("/goals")
    async def list_goals(request: Request, status: str | None = None) -> list[dict[str, Any]]:
        return J(request).store.list_goals(status=status)

    @app.get("/goals/{goal_id}")
    async def get_goal(request: Request, goal_id: str) -> dict[str, Any]:
        jig = J(request)
        return {**jig.store.get_goal(goal_id), "tasks": jig.store.list_tasks(goal_id=goal_id)}

    @app.post("/goals/{goal_id}/cancel")
    async def cancel_goal(request: Request, goal_id: str) -> dict[str, Any]:
        return J(request).cancel_goal(goal_id)

    # Tasks -----------------------------------------------------------------
    @app.post("/tasks", status_code=201)
    async def create_task(request: Request, body: TaskIn) -> dict[str, Any]:
        return J(request).create_task(title=body.title, description=body.description, mode=body.mode,
                                      delay_s=body.delay_s)

    @app.get("/tasks")
    async def list_tasks(request: Request, status: str | None = None, goal_id: str | None = None,
                         newest_first: bool = False, limit: int = Query(200, le=1000)) -> list[dict[str, Any]]:
        return J(request).store.list_tasks(status=status, goal_id=goal_id, newest_first=newest_first, limit=limit)

    @app.get("/tasks/{task_id}")
    async def get_task(request: Request, task_id: str) -> dict[str, Any]:
        jig = J(request)
        return {**jig.store.get_task(task_id), "runs": jig.store.runs_for_task(task_id)}

    @app.post("/tasks/{task_id}/cancel")
    async def cancel_task(request: Request, task_id: str) -> dict[str, Any]:
        return J(request).cancel_task(task_id)

    @app.post("/tasks/{task_id}/pause")
    async def pause_task(request: Request, task_id: str) -> dict[str, Any]:
        return await J(request).pause_task(task_id)

    @app.post("/tasks/{task_id}/resume")
    async def resume_task(request: Request, task_id: str) -> dict[str, Any]:
        return J(request).resume_task(task_id)

    @app.post("/tasks/{task_id}/retry")
    async def retry_task(request: Request, task_id: str, body: TaskRetryIn) -> dict[str, Any]:
        return J(request).retry_task(task_id, continue_anyway=body.continue_anyway)

    @app.get("/agent")
    async def agent_status(request: Request) -> dict[str, Any]:
        return J(request).agent_status()

    @app.post("/agent/pause")
    async def pause_agent(request: Request) -> dict[str, Any]:
        return await J(request).pause_agent()

    @app.post("/agent/resume")
    async def resume_agent(request: Request) -> dict[str, Any]:
        return J(request).resume_agent()

    @app.get("/runs")
    async def list_runs(request: Request, task_id: str | None = None, kind: str | None = None,
                        limit: int = Query(50, le=500)) -> list[dict[str, Any]]:
        return J(request).store.list_runs(task_id=task_id, kind=kind, limit=limit)

    @app.get("/runs/{run_id}")
    async def get_run(request: Request, run_id: str) -> dict[str, Any]:
        return J(request).store.get_run(run_id)

    # Schedules -------------------------------------------------------------
    @app.post("/schedules", status_code=201)
    async def create_schedule(request: Request, body: ScheduleIn) -> dict[str, Any]:
        jig = J(request)
        fields = body.model_dump()
        if body.repeat is not None and body.repeat.get("kind") != "interval" and not body.timezone:
            fields["timezone"] = jig.config.runtime.timezone
        s = jig.store.create_schedule(**fields, created_by="user")
        jig.audit.record("schedule.created", f"schedule {s['name']!r} created", actor="user", schedule_id=s["id"],
                         repeat=s["repeat"], timezone=s["timezone"], next_run_at=s["next_run_at"], **_who(request))
        jig.scheduler.wake()
        return s

    @app.get("/schedules")
    async def list_schedules(request: Request) -> list[dict[str, Any]]:
        return J(request).store.list_schedules()

    @app.patch("/schedules/{schedule_id}")
    async def patch_schedule(request: Request, schedule_id: str, body: SchedulePatch) -> dict[str, Any]:
        jig = J(request)
        changes = _set(body)
        if (changes.get("repeat") or {}).get("kind") not in (None, "interval") and not changes.get("timezone") \
                and not jig.store.get_schedule(schedule_id)["timezone"]:
            changes["timezone"] = jig.config.runtime.timezone
        s = jig.store.edit_schedule(schedule_id, **changes)
        action = "updated"
        if set(changes) == {"enabled"}:
            action = "resumed" if changes["enabled"] else "paused"
        jig.audit.record(f"schedule.{action}", f"schedule {s['name']!r} {action}", actor="user",
                         schedule_id=schedule_id, changes=changes, next_run_at=s["next_run_at"], **_who(request))
        jig.scheduler.wake()
        return s

    @app.delete("/schedules/{schedule_id}", status_code=204)
    async def delete_schedule(request: Request, schedule_id: str) -> None:
        jig = J(request)
        name = jig.store.get_schedule(schedule_id)["name"]
        jig.store.delete_schedule(schedule_id)
        jig.audit.record("schedule.deleted", f"schedule {name!r} deleted", actor="user", schedule_id=schedule_id,
                         **_who(request))

    # Approvals -------------------------------------------------------------
    @app.get("/approvals")
    async def list_approvals(request: Request, status: str | None = None) -> list[dict[str, Any]]:
        return J(request).approvals.list(status=status)

    @app.get("/approvals/{approval_id}")
    async def get_approval(request: Request, approval_id: str) -> dict[str, Any]:
        return J(request).approvals.get(approval_id)

    @app.post("/approvals/{approval_id}")
    async def respond_approval(request: Request, approval_id: str, body: ApprovalIn) -> dict[str, Any]:
        return J(request).approvals.respond(approval_id, approve=body.approve, note=body.note)

    # Rules -----------------------------------------------------------------
    @app.get("/rules")
    async def list_rules(request: Request) -> list[dict[str, Any]]:
        return J(request).rules.list()

    @app.get("/rules/core")
    async def core_rules() -> list[dict[str, Any]]:
        return [{"id": r.id, "decision": r.decision.value, "description": r.description, "overridable": False}
                for r in CORE_RULES]

    @app.post("/rules", status_code=201)
    async def create_rule(request: Request, body: RuleIn) -> dict[str, Any]:
        jig = J(request)
        rule = jig.rules.create(**body.model_dump())
        jig.audit.record("rule.created", f"rule {rule['tool']} -> {rule['decision']}", actor="user", rule=rule)
        jig.bus.publish(EventType.RULE_CHANGED, rule_id=rule["id"], action="created")
        return rule

    @app.get("/rules/{rule_id}")
    async def get_rule(request: Request, rule_id: str) -> dict[str, Any]:
        return J(request).rules.get(rule_id)

    @app.patch("/rules/{rule_id}")
    async def patch_rule(request: Request, rule_id: str, body: RulePatch) -> dict[str, Any]:
        jig = J(request)
        rule = jig.rules.update(rule_id, **_set(body))
        jig.audit.record("rule.updated", f"rule {rule_id} updated", actor="user", rule=rule)
        jig.bus.publish(EventType.RULE_CHANGED, rule_id=rule_id, action="updated")
        return rule

    @app.delete("/rules/{rule_id}", status_code=204)
    async def delete_rule(request: Request, rule_id: str) -> None:
        jig = J(request)
        jig.rules.delete(rule_id)
        jig.audit.record("rule.deleted", f"rule {rule_id} deleted", actor="user", rule_id=rule_id)
        jig.bus.publish(EventType.RULE_CHANGED, rule_id=rule_id, action="deleted")

    # Memory ----------------------------------------------------------------
    @app.get("/memory")
    async def list_memory(request: Request, q: str | None = None, limit: int = Query(100, le=1000),
                          offset: int = 0, kind: str | None = None) -> list[dict[str, Any]]:
        if q is not None:
            raise HTTPException(400, "search with POST /memory/search {\"q\": ...}: words in a web address can end "
                                     "up in logs")
        return J(request).memory.list(limit=limit, offset=offset, kind=kind)

    @app.post("/memory/search")
    async def search_memory(request: Request, body: MemorySearch) -> list[dict[str, Any]]:
        return J(request).memory.search(body.q, limit=body.limit)

    @app.post("/memory", status_code=201)
    async def add_memory(request: Request, body: MemoryIn) -> dict[str, Any]:
        jig = J(request)
        m = jig.memory.add(body.content, kind=body.kind, tags=body.tags, source="user")
        jig.audit.record("memory.added", f"memory {m['id']} added", actor="user", memory_id=m["id"])
        return m

    @app.get("/memory/{memory_id}")
    async def get_memory(request: Request, memory_id: int) -> dict[str, Any]:
        return J(request).memory.get(memory_id)

    @app.patch("/memory/{memory_id}")
    async def edit_memory(request: Request, memory_id: int, body: MemoryPatch) -> dict[str, Any]:
        jig = J(request)
        m = jig.memory.edit(memory_id, **_set(body))
        jig.audit.record("memory.edited", f"memory {memory_id} edited", actor="user", memory_id=memory_id)
        return m

    @app.delete("/memory/{memory_id}", status_code=204)
    async def forget_memory(request: Request, memory_id: int) -> None:
        jig = J(request)
        jig.memory.forget(memory_id)
        jig.audit.record("memory.forgotten", f"memory {memory_id} forgotten", actor="user", memory_id=memory_id)

    @app.post("/memory/wipe")
    async def wipe_memory(request: Request, body: MemoryWipeIn) -> dict[str, Any]:
        """Forget everything Jig remembers, with its search index, and with ``notes`` every note too. The audit
        entries have counts, never content."""
        _require_confirm(body, "Nothing was forgotten")
        jig = J(request)
        out = jig.memory.wipe()
        jig.audit.record("memory.wiped", f"all memories forgotten ({out['forgotten']})", actor="user",
                         count=out["forgotten"], wal_cleared=out["wal_cleared"], **_who(request))
        if body.notes:
            notes = jig.store.wipe_notes()
            jig.audit.record("note.wiped", f"all notes deleted ({notes['deleted']})", actor="user",
                             count=notes["deleted"], wal_cleared=notes["wal_cleared"], **_who(request))
            out = {**out, "notes_deleted": notes["deleted"], "wal_cleared": out["wal_cleared"] and notes["wal_cleared"]}
        return out

    # Notes: what Jig writes down for itself while it works -----------------
    @app.get("/notes")
    async def notes(request: Request, limit: int = Query(50, le=1000)) -> list[dict[str, Any]]:
        return J(request).store.list_notes(limit=limit)

    @app.post("/notes/wipe")
    async def wipe_notes(request: Request, body: ConfirmIn) -> dict[str, Any]:
        """Delete every note. The audit entry has the count, never content."""
        _require_confirm(body, "No note was deleted")
        jig = J(request)
        out = jig.store.wipe_notes()
        jig.audit.record("note.wiped", f"all notes deleted ({out['deleted']})", actor="user", count=out["deleted"],
                         wal_cleared=out["wal_cleared"], **_who(request))
        return out

    @app.get("/notes/{note_id}")
    async def get_note(request: Request, note_id: int) -> dict[str, Any]:
        return J(request).store.get_note(note_id)

    @app.patch("/notes/{note_id}")
    async def edit_note(request: Request, note_id: int, body: NotePatch) -> dict[str, Any]:
        jig = J(request)
        changes = _set(body)
        n = jig.store.edit_note(note_id, **changes)
        jig.audit.record("note.edited", f"note {note_id} edited", actor="user", note_id=note_id,
                         fields=sorted(changes), **_who(request))
        return n

    @app.delete("/notes/{note_id}", status_code=204)
    async def delete_note(request: Request, note_id: int) -> None:
        jig = J(request)
        jig.store.delete_note(note_id)
        jig.audit.record("note.deleted", f"note {note_id} deleted", actor="user", note_id=note_id, **_who(request))

    # Audit -----------------------------------------------------------------
    @app.get("/audit")
    async def audit(request: Request, kind: str | None = None, task_id: str | None = None,
                    run_id: str | None = None, after_id: int = 0, before_id: int | None = None,
                    newest_first: bool = False, limit: int = Query(200, le=5000)) -> list[dict[str, Any]]:
        rows = A(request).query(kind=kind, task_id=task_id, run_id=run_id, after_id=after_id,
                                      before_id=before_id, newest_first=newest_first, limit=limit)
        for r in rows:
            r["data"] = json.loads(r.pop("data_json"))
        return rows

    @app.get("/audit/older-with-content")
    async def audit_older_with_content(request: Request) -> dict[str, Any]:
        """History entries written by older Jig versions that can still quote conversations or jobs."""
        return J(request).audit.older_entries_with_content()

    # Vault (names and metadata only; values can be written but never read back) ----------
    @app.get("/vault")
    async def vault_list(request: Request) -> list[dict[str, Any]]:
        return J(request).vault.list()

    @app.put("/vault/{name}")
    async def vault_set(request: Request, name: str, body: SecretIn) -> dict[str, Any]:
        jig = J(request)
        if name.startswith(CONNECTOR_SECRET_PREFIX):
            raise HTTPException(400, f"{name!r} belongs to a connected account; use 'jig connect' or Settings > "
                                     "Connections, which store it with the connection")
        if name.startswith(MODEL_KEY_PREFIX) and body.allowed_tools:
            raise HTTPException(400, f"{name!r} is a model API key: it is only for Jig's connection to the model, "
                                     "so no tool may use it. Store it without allowed_tools.")
        s = jig.vault.set(name, body.value, allowed_tools=body.allowed_tools)
        jig.audit.record("vault.set", f"secret {name!r} stored", actor="user", secret=name,
                         allowed_tools=body.allowed_tools)
        return s

    @app.delete("/vault/{name}", status_code=204)
    async def vault_delete(request: Request, name: str) -> None:
        jig = J(request)
        if name.startswith(CONNECTOR_SECRET_PREFIX):
            raise HTTPException(400, f"{name!r} belongs to a connected account; disconnect it with 'jig disconnect' "
                                     "or Settings > Connections, which also revokes it at the provider")
        jig.vault.delete(name)
        jig.audit.record("vault.deleted", f"secret {name!r} deleted", actor="user", secret=name)

    # Connected accounts ------------------------------------------------------------------------
    # The latest sign-in per provider started from the web UI. A device sign-in's code is only shown to
    # this computer, where the sign-in was started.
    connect_attempts: dict[str, dict[str, Any]] = {}

    @app.get("/connections")
    async def connections(request: Request) -> list[dict[str, Any]]:
        local = _source(request).kind == "local"
        rows = J(request).connections.status()
        for r in rows:
            attempt = connect_attempts.get(r["provider"])
            if attempt is not None and not local:
                attempt = {k: v for k, v in attempt.items() if k not in ("user_code", "verification_uri")}
            r["attempt"] = attempt
            r["guide"] = connector_guide(r["provider"], install_url=r["install_url"])
            r["walkthrough"] = connector_walkthrough.walkthrough(r["provider"], install_url=r["install_url"])
        return rows

    @app.post("/connections/{name}/walkthrough/{check}")
    async def connection_walkthrough_check(request: Request, name: str, check: str,
                                           body: WalkthroughCheckIn) -> dict[str, Any]:
        """Check one guided set-up step for real (read-only). Only non-secret values are sent here."""
        _require_local(request, "Checking a connection step")
        _require_confirm(body, "Checking a connection step")
        _connector(name)
        try:
            return await connector_walkthrough.run_check(J(request), name, check, body.values)
        except JigError as exc:
            raise HTTPException(400, str(exc)) from None

    # Signal without a terminal: what's installed, a download at the person's request, and signal-cli's link
    # step run here with its link shown as a QR code (jig.connectors.signal_setup).
    class _CurrentAudit:
        def record(self, *args: Any, **kwargs: Any) -> None:
            controller.current.audit.record(*args, **kwargs)

    signal_tools = signal_setup.tools_dir(config.data_dir)
    signal_download = signal_setup.Downloads(signal_tools, _CurrentAudit())
    signal_link = signal_setup.Link(signal_tools, _CurrentAudit(), _qr_data_uri)
    app.state.signal_jobs = (signal_download, signal_link)

    @app.get("/connections/signal/setup")
    async def signal_setup_status(request: Request) -> dict[str, Any]:
        _require_local(request, "Setting up Signal")
        return {**await signal_setup.status(signal_tools), "download": signal_download.state,
                "link": signal_link.state}

    @app.post("/connections/signal/setup/download")
    async def signal_setup_download(request: Request, body: ConfirmIn) -> dict[str, Any]:
        """Download signal-cli (and Java, if none is new enough) into the data folder's tools, checked against
        their publishers' checksums. Answers at once; GET /connections/signal/setup shows the progress."""
        _require_local(request, "Downloading signal-cli")
        _require_confirm(body, "Downloading signal-cli")
        return signal_download.start()

    @app.post("/connections/signal/setup/link")
    async def signal_setup_link(request: Request, body: SignalLinkIn) -> dict[str, Any]:
        """Run signal-cli's link step. GET /connections/signal/setup shows its QR code, then the linked number."""
        _require_local(request, "Linking Signal")
        _require_confirm(body, "Linking Signal")
        return signal_link.start(body.signal_cli)

    @app.post("/connections/signal/setup/link/cancel")
    async def signal_setup_link_cancel(request: Request, body: ConfirmIn) -> dict[str, Any]:
        _require_local(request, "Linking Signal")
        _require_confirm(body, "Stopping the Signal link")
        await signal_link.close()
        return signal_link.state

    def _connector(name: str):
        try:
            return connector_provider(name)
        except ConnectorError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post("/connections/{name}/connect")
    async def connection_connect(request: Request, name: str, body: ConnectIn) -> dict[str, Any]:
        """Connect an account from this computer. A browser sign-in returns the link for the UI to open, and a
        device sign-in the code to show; both complete in the background. A token sign-in checks what was
        typed and stores it in the vault before answering. Typed values are never echoed, logged or audited."""
        _require_local(request, "Connecting an account")
        _require_confirm(body, "Connecting an account")
        jig = J(request)
        spec = _connector(name)
        method = body.method or spec.kind
        if method not in spec.methods:
            raise HTTPException(400, f"{spec.label} connects by {' or '.join(spec.methods)}, not {method!r}")
        if body.access is not None and body.access not in spec.access_levels:
            raise HTTPException(400, f"{spec.label} access must be one of {list(spec.access_levels)}")
        if method == "token":
            values = body.values if isinstance(body.values, dict) else None
            if values is None or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                raise HTTPException(400, f"{spec.label}: send what it asks for as text fields in \"values\"")
            known = {i.name for i in spec.inputs}
            if set(values) - known:
                raise HTTPException(400, f"{spec.label} asks for {sorted(known)} only")
            try:
                result = await connect_account(name, access=body.access, store=jig.connections, http=jig.http,
                                               values=values, method="token", via="api")
            except JigError as exc:
                message = str(exc)
                jig.audit.record("connector.connect_failed", f"{spec.label}: {message}", actor="user",
                                 provider=name, error=message, via="api")
                raise HTTPException(400, message) from None
            finally:
                values.clear()
            connect_attempts.pop(name, None)
            return {"provider": name, "connected": True, "account": result["connection"]["account"],
                    "not_granted": result["not_granted"]}
        client = jig.connections.client_state(spec)
        if not client["configured"]:
            raise HTTPException(409, client["problem"])
        started = now_iso()
        shown: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        connect_attempts[name] = {"status": "waiting", "method": method, "started": started, "error": None}

        def ready_link(url: str) -> None:
            if not shown.done():
                shown.set_result({"auth_url": url})

        def show_code(info: dict[str, Any]) -> None:
            connect_attempts[name] = {**connect_attempts[name], "user_code": info["user_code"],
                                      "verification_uri": info["verification_uri"],
                                      "expires_in": info["expires_in"]}
            if not shown.done():
                shown.set_result(dict(info))

        async def flow() -> None:
            try:
                if method == "device":
                    await connect_account(name, access=body.access, store=jig.connections, http=jig.http,
                                          show_code=show_code, method="device", via="api")
                else:
                    await connect_account(name, access=body.access, store=jig.connections, http=jig.http,
                                          open_browser=lambda _url: None, ready=ready_link, method=method,
                                          via="api")
                connect_attempts[name] = {"status": "connected", "method": method, "started": started,
                                          "error": None}
            except Exception as exc:  # reported to the UI and the audit log, never swallowed
                message = f"{type(exc).__name__}: {exc}"
                connect_attempts[name] = {"status": "failed", "method": method, "started": started,
                                          "error": message}
                jig.audit.record("connector.connect_failed", f"{spec.label}: {message}", actor="user",
                                 provider=name, error=message, via="api")
                if not shown.done():
                    shown.set_exception(exc)

        jig._spawn(flow())
        try:
            out = await asyncio.wait_for(asyncio.shield(shown), 30)
        except (JigError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None
        return {"provider": name, "method": method, **out}

    @app.post("/connections/{family}/client")
    async def connection_client(request: Request, family: str, body: ClientIn) -> dict[str, Any]:
        """Store the app a provider family signs in with: Google's client file (its text, read in the browser
        and sent here), or the client ID of a Microsoft app registration. It goes straight into the vault and
        is never echoed back."""
        _require_local(request, "Setting up an app for connecting accounts")
        _require_confirm(body, "Setting up an app for connecting accounts")
        jig = J(request)
        if family == google_connector.FAMILY:
            if not isinstance(body.client_json, str) or not body.client_json.strip():
                raise HTTPException(400, "send the downloaded client file's text as \"client_json\"")
            try:
                client = google_connector.client_from_text(body.client_json)
            except ConnectorError as exc:
                raise HTTPException(400, str(exc)) from None
        elif family == microsoft_connector.FAMILY:
            if not isinstance(body.client_id, str) or not (body.tenant is None or isinstance(body.tenant, str)):
                raise HTTPException(400, "send your app registration's Application (client) ID as \"client_id\"")
            try:
                client = microsoft_connector.client_from_id(body.client_id, body.tenant or "")
            except ConnectorError as exc:
                raise HTTPException(400, str(exc)) from None
        else:
            raise HTTPException(404, f"only google and microsoft take an app here, not {family!r}")
        jig.connections.set_client(family, client, via="api")
        return {"family": family, "stored": True, "client_id": client["client_id"]}

    @app.delete("/connections/{family}/client")
    async def connection_client_delete(request: Request, family: str) -> dict[str, Any]:
        _require_local(request, "Removing an app for connecting accounts")
        jig = J(request)
        if family not in (google_connector.FAMILY, microsoft_connector.FAMILY):
            raise HTTPException(404, f"only google and microsoft take an app here, not {family!r}")
        users = [r["label"] for r in jig.connections.status() if r["family"] == family and r["status"] != "not_connected"]
        if users:
            raise HTTPException(409, f"disconnect {', '.join(users)} first; they use this app")
        return {"family": family, "deleted": jig.connections.delete_client(family, via="api")}

    @app.post("/connections/{name}/disconnect")
    async def connection_disconnect(request: Request, name: str, body: ConfirmIn) -> dict[str, Any]:
        _require_confirm(body, "Disconnecting an account")
        jig = J(request)
        _connector(name)
        connect_attempts.pop(name, None)
        return await jig.connectors.disconnect(name, via="api")

    # Turning Jig off ---------------------------------------------------------------------------
    @app.exception_handler(PowerRefused)
    async def power_refused(_: Request, exc: PowerRefused) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(ModelServerNotManaged)
    @app.exception_handler(RemoteError)
    async def refused_conflict(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/power")
    async def power(request: Request) -> dict[str, Any]:
        """Whether Jig manages the model server, what stopping it would free, and how Jig starts again."""
        out = await asyncio.to_thread(power_state, controller.config, controller.current.model_server)
        if out.get("autostart") is not None:
            out["start_again"] = start_again(out["autostart"], tray=DESKTOP["installed"])
        return out

    @app.post("/power/stop", status_code=202)
    async def power_stop(request: Request, body: PowerStopIn) -> JSONResponse:
        """Turn Jig off (scope "jig") or Jig and the model server it launched ("jig_and_model"). Replies first,
        then shuts down gracefully. Autostart is left as it is. Works in set-up mode too."""
        jig = controller.current
        check_stop(controller.config, jig.model_server, body.scope, body.confirm)
        request_exit = getattr(request.app.state, "request_exit", None)
        if request_exit is None:
            raise HTTPException(409, "This Jig was not started with 'jig serve', so it can't turn itself off. Stop "
                                     "the program that runs it.")
        autostart = await asyncio.to_thread(autostart_summary, config)
        model = jig.model_server.info()
        if not model["managed"]:
            model_action = "not managed by Jig; left as it is"
        elif body.scope == "jig":
            model_action = f"left running (pid {model['pid']}); the next Jig start supervises it again"
        else:
            model_action = f"stopping (pid {model['pid']}), which frees its GPU memory"
        jig.audit.record("power.stop", f"turn off requested ({body.scope})", actor="user", scope=body.scope, via="api",
                         model_server=model_action, autostart_registered=autostart.get("registered"),
                         **_who(request))
        request.app.state.stop_request = {"scope": body.scope, "via": "api"}
        jig.bus.publish(EventType.POWER_STOPPING, scope=body.scope)
        what = "Jig and the model server are" if body.scope == "jig_and_model" and model["managed"] else "Jig is"
        again = start_again(autostart, tray=DESKTOP["installed"])
        return JSONResponse({"stopping": True, "scope": body.scope, "model_server": model_action,
                             "autostart": autostart, "start_again": again, "message": f"{what} turning off. {again}"},
                            status_code=202, background=BackgroundTask(request_exit, "POST /power/stop"))

    @app.get("/model")
    async def model_status(request: Request) -> dict[str, Any]:
        sup = J(request).model_server
        base_url = J(request).config.model.base_url
        out = {"base_url": base_url, "configured": sup.configured, **sup.info()}
        if sup.managed:
            out["gpu"] = await asyncio.to_thread(gpu_usage, sup.process.pid)  # type: ignore[union-attr]
        else:
            out["refusal"] = sup._not_managed_reason(base_url)
        return out

    @app.post("/model/stop")
    async def model_stop(request: Request, body: ConfirmIn) -> dict[str, Any]:
        """Stop the model server Jig launched, and keep Jig running. Refuses any server Jig didn't start."""
        _require_confirm(body, "The model server was not stopped")
        jig = J(request)
        pid = await jig.model_server.stop_by_user(jig.config.model.base_url)
        jig.audit.record("model_server.stopped", f"stopped the model server Jig launched (pid {pid})", actor="user",
                         pid=pid, via="api", **_who(request))
        return {**jig.model_server.info(), "stopped": True, "pid": pid,
                "message": "The model server is stopped. Jig keeps running, but can't answer until you start it "
                           "again with 'jig model start' (or turn Jig off and on)."}

    @app.post("/model/start")
    async def model_start(request: Request) -> dict[str, Any]:
        """Launch the [model.launch] server again and wait until it is ready."""
        jig = J(request)
        info = await jig.model_server.start_by_user()
        kind = "model_server.launched" if info["managed"] else "model_server.already_running"
        jig.audit.record(kind, f"model server started by the user (pid {info['pid']})" if info["managed"] else
                         "model server already running; not launching", actor="user", via="api", **info,
                         **_who(request))
        return {"started": info["managed"], **info}

    # Use Jig from your other devices -------------------------------------------------------------
    @app.get("/remote")
    async def remote_status(request: Request) -> dict[str, Any]:
        out = await asyncio.to_thread(remote.status, port)
        out["devices"] = len(devices.list())
        return out

    @app.post("/remote/enable")
    async def remote_enable(request: Request, body: ConfirmIn) -> dict[str, Any]:
        """Run 'tailscale serve' for Jig (your tailnet only, never a funnel) and record the tailnet origin."""
        _require_local(request, "Turning on remote access")
        _require_confirm(body, "Remote access was not turned on")
        out = await asyncio.to_thread(remote.enable, port)
        A(request).record("remote.enabled", f"remote access on at {out['url']}", actor="user", url=out["url"],
                                allowed_logins=out["allowed_logins"], **_who(request))
        return out

    @app.post("/remote/disable")
    async def remote_disable(request: Request, body: ConfirmIn) -> dict[str, Any]:
        _require_confirm(body, "Remote access was not turned off")
        out = await asyncio.to_thread(remote.disable, port)
        A(request).record("remote.disabled", "remote access off", actor="user",
                                removed_serve_entry=out["removed_serve_entry"], **_who(request))
        return out

    @app.get("/devices")
    async def list_devices(request: Request, include_revoked: bool = False) -> list[dict[str, Any]]:
        principal: Principal = request.scope["state"]["principal"]
        current = principal.device["id"] if principal.device else None
        return [{**d, "current": d["id"] == current} for d in devices.list(include_revoked=include_revoked)]

    @app.post("/devices/pairing", status_code=201)
    async def create_pairing(request: Request, body: PairingIn) -> dict[str, Any]:
        """'Add a device': a one-time pairing code (5 minutes) and a QR code of the pairing URL."""
        _require_local(request, "Adding a device")
        pairing = devices.new_pairing(expires_in_days=body.expires_in_days)
        origin = remote.origin() or _source(request).origin
        url = f"{origin}/#pair={pairing['code']}"
        A(request).record("device.pairing_created", "pairing code created", actor="user", origin=origin,
                                device_expires_in_days=body.expires_in_days, **_who(request))
        return {**pairing, "url": url, "qr_svg_data_uri": _qr_data_uri(url), "remote_enabled": bool(remote.origin())}

    @app.delete("/devices/{device_id}")
    async def revoke_device(request: Request, device_id: str) -> dict[str, Any]:
        device = devices.revoke(device_id)
        A(request).record("device.revoked", f"device {device['name']!r} revoked", actor="user",
                                device_id=device_id, **_who(request))
        return device

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
