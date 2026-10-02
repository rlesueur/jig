"""``/autostart`` API routes. They sit behind the same token / session authentication as every other route.

``JIG_AUTOSTART_ENTRY`` overrides the entry name (the tests use it so they never touch the real task).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import Config
from . import AutostartError, backend_from_config, disable, enable, not_applicable_reason
from .base import CONTAINER_MODE_HINT


class AutostartEnableIn(BaseModel):
    # Required, and must be true: the caller confirms that the user has seen the plan from GET /autostart.
    confirm: bool
    start_now: bool = False


def autostart_router(config: Config) -> APIRouter:
    router = APIRouter(prefix="/autostart", tags=["autostart"])
    reason = not_applicable_reason(config)

    def backend():
        if reason:
            raise HTTPException(409, reason)
        return backend_from_config(config)

    @router.get("")
    def autostart_status() -> dict[str, Any]:
        """Whether autostart is registered, its last run and result, and the plan enable would register.
        In container mode: ``applicable`` is false, with the reason."""
        if reason:
            return {"applicable": False, "reason": reason, "hint": CONTAINER_MODE_HINT,
                    "deployment": config.deployment}
        b = backend()
        try:
            return {"applicable": True, "status": b.status().as_dict(), "plan": b.plan().as_dict()}
        except AutostartError as exc:
            raise HTTPException(500, str(exc)) from exc

    @router.post("/enable")
    def autostart_enable(body: AutostartEnableIn) -> dict[str, Any]:
        b = backend()
        if body.confirm is not True:
            raise HTTPException(400, "autostart was not enabled: send \"confirm\": true after showing the user "
                                     "the plan from GET /autostart")
        owner = b.registered_owner()
        if owner is not None and not b.owned(owner):
            raise HTTPException(409, b.other_install_message(owner))
        if owner is not None:
            raise HTTPException(409, f"autostart is already registered as {b.entry}")
        try:
            plan = enable(b, confirmed=True, via="api", start_now=body.start_now)
        except AutostartError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"enabled": True, "plan": plan.as_dict()}

    @router.post("/disable")
    def autostart_disable() -> dict[str, Any]:
        b = backend()
        owner = b.registered_owner()
        if owner is not None and not b.owned(owner):
            raise HTTPException(409, b.other_install_message(owner))
        try:
            return {"enabled": False, "removed": disable(b, via="api")}
        except AutostartError as exc:
            raise HTTPException(500, str(exc)) from exc

    return router
