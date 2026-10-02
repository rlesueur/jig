"""Code and shell execution inside the container sandbox. Registered only with ``[sandbox] backend = "container"``.

Both tools are side-effecting: every call goes through the core rules, custom rules, the Sentinel and,
by default, the user's approval. Network access from the command is open only while it runs, and only
through the egress proxy.

Output longer than one result holds is shown as its start and its end, and the whole of what was captured is
saved in the workspace, so the model can read the middle with read_file (offset or find).
"""

from __future__ import annotations

from typing import Any

from ..constants import Decision, Effect, ToolCategory
from ..db import new_id
from ..errors import ToolArgumentError
from .registry import ToolContext, ToolRegistry

MAX_TIMEOUT_S = 170
# How much of each stream a result shows: its first SHOWN_HEAD and last SHOWN_TAIL characters.
SHOWN_HEAD = 4_000
SHOWN_TAIL = 16_000
OUTPUT_DIR = ".jig-output"


def _timeout(ctx: ToolContext, timeout_s: int) -> int:
    ceiling = min(MAX_TIMEOUT_S, int(ctx.config.sandbox.command_timeout_s))
    if timeout_s < 1 or timeout_s > ceiling:
        raise ToolArgumentError(f"timeout_s must be between 1 and {ceiling}")
    return timeout_s


def shown(ctx: ToolContext, tool: str, result: dict[str, Any]) -> dict[str, Any]:
    """The result with each long stream cut to its start and end, saying where the whole of it was saved."""
    out = dict(result)
    notes: list[str] = []
    saved: dict[str, str] = {}
    call = new_id(tool)
    for stream in ("stdout", "stderr"):
        text = out.get(stream) or ""
        dropped = int(out.pop(f"{stream}_dropped", 0) or 0)
        if len(text) <= SHOWN_HEAD + SHOWN_TAIL and not dropped:
            continue
        path = saved[stream] = f"{OUTPUT_DIR}/{call}.{stream}.txt"
        target = ctx.sandbox.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        if len(text) > SHOWN_HEAD + SHOWN_TAIL:
            cut = len(text) - SHOWN_HEAD - SHOWN_TAIL
            out[stream] = (text[:SHOWN_HEAD] + f"\n[... {cut} characters not shown here: read them from {path} "
                           f"with read_file, offset={SHOWN_HEAD} ...]\n" + text[-SHOWN_TAIL:])
        lost = (f" Its first {dropped} bytes were not kept, because the output was longer than the sandbox "
                "captures." if dropped else "")
        notes.append(f"{stream} is {len(text)} characters; the whole of it is saved in {path}: read it with "
                     f"read_file (offset, or find).{lost}")
    out["truncated"] = bool(saved)
    if saved:
        out["output_files"] = saved
        out["note"] = " ".join(notes) + " Do not guess what the part not shown says."
    return out


def register_exec_tools(registry: ToolRegistry) -> None:
    tool = registry.tool

    @tool(
        description="Run a shell command (sh) in the isolated Linux container sandbox, in /workspace (the agent's "
        "workspace). Network access goes through Jig's egress proxy. Long output is shown as its start and end, "
        "with the whole of it saved in the workspace to read with read_file. This is an action: it is reviewed by "
        "the Sentinel and needs the user's approval unless a rule allows it.",
        effect=Effect.SIDE_EFFECT,
        category=ToolCategory.CODE,
        outbound=True,
        default_decision=Decision.ASK,
        args={"command": "Shell command to run.", "timeout_s": f"Time limit in seconds (1-{MAX_TIMEOUT_S})."},
    )
    async def run_command(ctx: ToolContext, command: str, timeout_s: int = 60) -> dict[str, Any]:
        limit = _timeout(ctx, timeout_s)
        async with ctx.container.lease(tool="run_command", run_id=ctx.run_id, task_id=ctx.task_id):
            return shown(ctx, "run_command", await ctx.container.exec(["sh", "-c", command], timeout_s=limit))

    @tool(
        description="Run a Python 3 script in the isolated Linux container sandbox, in /workspace (the agent's "
        "workspace). Network access goes through Jig's egress proxy. Long output is shown as its start and end, "
        "with the whole of it saved in the workspace to read with read_file. This is an action: it is reviewed by "
        "the Sentinel and needs the user's approval unless a rule allows it.",
        effect=Effect.SIDE_EFFECT,
        category=ToolCategory.CODE,
        outbound=True,
        default_decision=Decision.ASK,
        args={"code": "Python source to run.", "timeout_s": f"Time limit in seconds (1-{MAX_TIMEOUT_S})."},
    )
    async def run_python(ctx: ToolContext, code: str, timeout_s: int = 60) -> dict[str, Any]:
        limit = _timeout(ctx, timeout_s)
        async with ctx.container.lease(tool="run_python", run_id=ctx.run_id, task_id=ctx.task_id):
            return shown(ctx, "run_python",
                         await ctx.container.exec(["python3", "-"], stdin=code.encode("utf-8"), timeout_s=limit))
