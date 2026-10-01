"""Code and shell execution inside the container sandbox. Registered only with ``[sandbox] backend = "container"``.

Both tools are side-effecting: every call goes through the core rules, custom rules, the Sentinel and,
by default, the user's approval. Network access from the command is open only while it runs, and only
through the egress proxy.
"""

from __future__ import annotations

from typing import Any

from ..constants import Decision, Effect, ToolCategory
from ..errors import ToolArgumentError
from .registry import ToolContext, ToolRegistry

MAX_TIMEOUT_S = 170


def _timeout(ctx: ToolContext, timeout_s: int) -> int:
    ceiling = min(MAX_TIMEOUT_S, int(ctx.config.sandbox.command_timeout_s))
    if timeout_s < 1 or timeout_s > ceiling:
        raise ToolArgumentError(f"timeout_s must be between 1 and {ceiling}")
    return timeout_s


def register_exec_tools(registry: ToolRegistry) -> None:
    tool = registry.tool

    @tool(
        description="Run a shell command (sh) in the isolated Linux container sandbox, in /workspace (the agent's "
        "workspace). Network access goes through Jig's egress proxy. This is an action: it is reviewed by the "
        "Sentinel and needs the user's approval unless a rule allows it.",
        effect=Effect.SIDE_EFFECT,
        category=ToolCategory.CODE,
        outbound=True,
        default_decision=Decision.ASK,
        args={"command": "Shell command to run.", "timeout_s": f"Time limit in seconds (1-{MAX_TIMEOUT_S})."},
    )
    async def run_command(ctx: ToolContext, command: str, timeout_s: int = 60) -> dict[str, Any]:
        limit = _timeout(ctx, timeout_s)
        async with ctx.container.lease(tool="run_command", run_id=ctx.run_id, task_id=ctx.task_id):
            return await ctx.container.exec(["sh", "-c", command], timeout_s=limit)

    @tool(
        description="Run a Python 3 script in the isolated Linux container sandbox, in /workspace (the agent's "
        "workspace). Network access goes through Jig's egress proxy. This is an action: it is reviewed by the "
        "Sentinel and needs the user's approval unless a rule allows it.",
        effect=Effect.SIDE_EFFECT,
        category=ToolCategory.CODE,
        outbound=True,
        default_decision=Decision.ASK,
        args={"code": "Python source to run.", "timeout_s": f"Time limit in seconds (1-{MAX_TIMEOUT_S})."},
    )
    async def run_python(ctx: ToolContext, code: str, timeout_s: int = 60) -> dict[str, Any]:
        limit = _timeout(ctx, timeout_s)
        async with ctx.container.lease(tool="run_python", run_id=ctx.run_id, task_id=ctx.task_id):
            return await ctx.container.exec(["python3", "-"], stdin=code.encode("utf-8"), timeout_s=limit)
