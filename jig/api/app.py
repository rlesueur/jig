"""HTTP API: chat, goals, tasks, schedules, approvals, rules, memory, notes, audit, vault and events.

Every route needs the API token (see ``jig.auth``) except ``/health``, the web
UI's static files and the browser session exchange.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..auth import Auth, AuthMiddleware, TokenStore, clear_cookie_header, session_cookie_header
from ..autostart.api import autostart_router
from ..config import Config
from ..constants import EventType, Mode
from ..errors import ConfigError
from ..errors import ModelServerUnavailable, NotFound, SecretNotFound, ToolArgumentError
from ..events import SubscriberOverflow
from ..policy.approvals import ApprovalConflict
from ..policy.core import CORE_RULES
from ..runtime import Jig


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None
    mode: Mode = Mode.ACTION


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
    interval_s: float
    start_in_s: float = 0.0


class SchedulePatch(BaseModel):
    enabled: bool | None = None
    prompt: str | None = None
    interval_s: float | None = None


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


class MemoryPatch(BaseModel):
    content: str | None = None
    kind: str | None = None
    tags: list[str] | None = None


class SecretIn(BaseModel):
    value: str = Field(min_length=1)
    allowed_tools: list[str] = []


class SessionIn(BaseModel):
    token: str | None = None
    code: str | None = None


def _set(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_unset=True)


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AVATAR_JS = Path(__file__).resolve().parents[2] / "avatar" / "jig-avatar.js"

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
    auth = Auth(tokens)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        jig = Jig(config, start_reason=start_reason)
        await jig.start()
        app.state.jig = jig
        try:
            yield
        finally:
            await jig.stop()

    app = FastAPI(title="Jig", version=__version__, lifespan=lifespan)
    app.state.auth = auth
    app.add_middleware(AuthMiddleware, auth=auth)

    def J(request: Request) -> Jig:
        return request.app.state.jig

    app.include_router(autostart_router(config))

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
    async def create_session(body: SessionIn) -> JSONResponse:
        """Exchange the API token or a one-time login code for an HttpOnly session cookie."""
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
                            headers={"Set-Cookie": session_cookie_header(value, max_age)})

    @app.get("/auth/session")
    async def get_session(request: Request) -> dict[str, Any]:
        principal = auth.authenticate({k.lower(): v for k, v in request.headers.items()})
        return {"authenticated": principal is not None, "via": principal.via if principal else None}

    @app.post("/auth/logout")
    async def logout() -> JSONResponse:
        return JSONResponse({"authenticated": False}, headers={"Set-Cookie": clear_cookie_header()})

    @app.post("/auth/login-code")
    async def login_code() -> dict[str, Any]:
        """A one-time code for signing a browser in (used by 'jig ui'); it never carries the token itself."""
        code, ttl = auth.new_login_code()
        return {"code": code, "expires_in": ttl}

    @app.exception_handler(NotFound)
    @app.exception_handler(SecretNotFound)
    async def not_found(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    @app.exception_handler(ToolArgumentError)
    async def bad_request(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(ApprovalConflict)
    async def conflict(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ModelServerUnavailable)
    async def model_down(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=503)

    # Health and state ------------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Public liveness check. It says only that the server is up: no versions, models or paths."""
        return {"status": "ok"}

    @app.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        jig = J(request)
        model = await jig.model.health()
        sentinel = await jig.sentinel_model.health()
        return {
            "status": "ok",
            "version": __version__,
            "model_endpoint": config.model.base_url,
            "sentinel_endpoint": config.sentinel.base_url,
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

    # Events ----------------------------------------------------------------
    @app.websocket("/events")
    async def events_ws(ws: WebSocket) -> None:
        jig: Jig = ws.app.state.jig
        await ws.accept()
        sub = jig.bus.subscribe()
        try:
            await ws.send_json({"type": EventType.AVATAR_STATE.value, "snapshot": True, "data": jig.tracker.current})
            while True:
                event = await sub.get()
                await ws.send_json(event.as_dict())
        except SubscriberOverflow as exc:
            await ws.close(code=1013, reason=str(exc))
        except WebSocketDisconnect:
            pass
        finally:
            sub.close()

    @app.get("/events/sse")
    async def events_sse(request: Request) -> StreamingResponse:
        jig = J(request)
        sub = jig.bus.subscribe()

        async def gen() -> AsyncIterator[str]:
            try:
                snapshot = {"type": EventType.AVATAR_STATE.value, "snapshot": True, "data": jig.tracker.current}
                yield f"data: {json.dumps(snapshot)}\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(sub.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"event: {event.type}\ndata: {json.dumps(event.as_dict(), default=str)}\n\n"
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
        stream = jig.chat(body.message, session_id=body.session_id, mode=body.mode)
        first = await anext(stream)

        async def gen() -> AsyncIterator[str]:
            yield json.dumps(first) + "\n"
            async for item in stream:
                yield json.dumps(item, default=str) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.get("/sessions/{session_id}")
    async def session(request: Request, session_id: str) -> dict[str, Any]:
        return {"id": session_id, "messages": J(request).store.get_session(session_id)}

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
        s = jig.store.create_schedule(**body.model_dump())
        jig.audit.record("schedule.created", f"schedule {s['name']!r} created", actor="user", schedule_id=s["id"])
        jig.scheduler.wake()
        return s

    @app.get("/schedules")
    async def list_schedules(request: Request) -> list[dict[str, Any]]:
        return J(request).store.list_schedules()

    @app.patch("/schedules/{schedule_id}")
    async def patch_schedule(request: Request, schedule_id: str, body: SchedulePatch) -> dict[str, Any]:
        jig = J(request)
        s = jig.store.update_schedule(schedule_id, **_set(body))
        jig.audit.record("schedule.updated", f"schedule {s['name']!r} updated", actor="user",
                         schedule_id=schedule_id, changes=_set(body))
        return s

    @app.delete("/schedules/{schedule_id}", status_code=204)
    async def delete_schedule(request: Request, schedule_id: str) -> None:
        jig = J(request)
        jig.store.delete_schedule(schedule_id)
        jig.audit.record("schedule.deleted", "schedule deleted", actor="user", schedule_id=schedule_id)

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
        jig = J(request)
        if q:
            return jig.memory.search(q, limit=limit)
        return jig.memory.list(limit=limit, offset=offset, kind=kind)

    @app.post("/memory", status_code=201)
    async def add_memory(request: Request, body: MemoryIn) -> dict[str, Any]:
        jig = J(request)
        m = jig.memory.add(body.content, kind=body.kind, tags=body.tags, source="user")
        jig.audit.record("memory.added", f"memory {m['id']} added", actor="user", memory_id=m["id"])
        jig.bus.publish(EventType.MEMORY_CHANGED, memory_id=m["id"], action="added")
        return m

    @app.get("/memory/{memory_id}")
    async def get_memory(request: Request, memory_id: int) -> dict[str, Any]:
        return J(request).memory.get(memory_id)

    @app.patch("/memory/{memory_id}")
    async def edit_memory(request: Request, memory_id: int, body: MemoryPatch) -> dict[str, Any]:
        jig = J(request)
        m = jig.memory.edit(memory_id, **_set(body))
        jig.audit.record("memory.edited", f"memory {memory_id} edited", actor="user", memory_id=memory_id)
        jig.bus.publish(EventType.MEMORY_CHANGED, memory_id=memory_id, action="edited")
        return m

    @app.delete("/memory/{memory_id}", status_code=204)
    async def forget_memory(request: Request, memory_id: int) -> None:
        jig = J(request)
        jig.memory.forget(memory_id)
        jig.audit.record("memory.forgotten", f"memory {memory_id} forgotten", actor="user", memory_id=memory_id)
        jig.bus.publish(EventType.MEMORY_CHANGED, memory_id=memory_id, action="forgotten")

    @app.get("/notes")
    async def notes(request: Request, limit: int = 50) -> list[dict[str, Any]]:
        return J(request).store.list_notes(limit=limit)

    # Audit -----------------------------------------------------------------
    @app.get("/audit")
    async def audit(request: Request, kind: str | None = None, task_id: str | None = None,
                    run_id: str | None = None, after_id: int = 0, before_id: int | None = None,
                    newest_first: bool = False, limit: int = Query(200, le=5000)) -> list[dict[str, Any]]:
        rows = J(request).audit.query(kind=kind, task_id=task_id, run_id=run_id, after_id=after_id,
                                      before_id=before_id, newest_first=newest_first, limit=limit)
        for r in rows:
            r["data"] = json.loads(r.pop("data_json"))
        return rows

    # Vault (names and metadata only; values can be written but never read back) ----------
    @app.get("/vault")
    async def vault_list(request: Request) -> list[dict[str, Any]]:
        return J(request).vault.list()

    @app.put("/vault/{name}")
    async def vault_set(request: Request, name: str, body: SecretIn) -> dict[str, Any]:
        jig = J(request)
        s = jig.vault.set(name, body.value, allowed_tools=body.allowed_tools)
        jig.audit.record("vault.set", f"secret {name!r} stored", actor="user", secret=name,
                         allowed_tools=body.allowed_tools)
        return s

    @app.delete("/vault/{name}", status_code=204)
    async def vault_delete(request: Request, name: str) -> None:
        jig = J(request)
        jig.vault.delete(name)
        jig.audit.record("vault.deleted", f"secret {name!r} deleted", actor="user", secret=name)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
