"""Shared names used across the runtime, the API and the avatar.

The avatar component (built separately in ``avatar/``) renders the states in
``AvatarState`` and the variants in ``TaskVariant``. Keep this module as the
single source of truth so the two sides stay aligned.

The avatar's own canonical names are ``monitoring`` (for ``sleeping``) and
``approval`` (for ``needs-approval``); it accepts the names below as aliases.
"""

from __future__ import annotations

from enum import StrEnum


class AvatarState(StrEnum):
    IDLE = "idle"
    SLEEPING = "sleeping"  # background read-only monitoring only
    THINKING = "thinking"
    WORKING = "working"  # always paired with a TaskVariant
    TALKING = "talking"
    NEEDS_APPROVAL = "needs-approval"
    SUCCESS = "success"
    ERROR = "error"


class TaskVariant(StrEnum):
    BROWSING = "browsing"
    WRITING = "writing"
    CODING = "coding"
    SHOPPING = "shopping"
    SCHEDULING = "scheduling"


class ToolCategory(StrEnum):
    """What a tool is about. Drives the avatar's ``working`` variant."""

    WEB = "web"
    FILES = "files"
    NOTES = "notes"
    MEMORY = "memory"
    CODE = "code"
    COMMERCE = "commerce"
    TIME = "time"
    CREDENTIALS = "credentials"


CATEGORY_TO_VARIANT: dict[ToolCategory, TaskVariant] = {
    ToolCategory.WEB: TaskVariant.BROWSING,
    ToolCategory.FILES: TaskVariant.WRITING,
    ToolCategory.NOTES: TaskVariant.WRITING,
    ToolCategory.MEMORY: TaskVariant.WRITING,
    ToolCategory.CODE: TaskVariant.CODING,
    ToolCategory.COMMERCE: TaskVariant.SHOPPING,
    ToolCategory.TIME: TaskVariant.SCHEDULING,
    ToolCategory.CREDENTIALS: TaskVariant.WRITING,
}

# Transient states (success / error) are shown for this long before the
# avatar settles back to whatever is still going on.
TRANSIENT_STATE_SECONDS = 3.0


class Effect(StrEnum):
    """What a tool does to the world. Enforced by the gate, not the prompt."""

    READ = "read"  # reads only
    PRIVATE_WRITE = "private_write"  # writes the agent's own notes / memory
    SIDE_EFFECT = "side_effect"  # changes something the user would care about


class Mode(StrEnum):
    RESEARCH = "research"  # proactive, read-only: READ and PRIVATE_WRITE tools only
    ACTION = "action"


RESEARCH_ALLOWED_EFFECTS = frozenset({Effect.READ, Effect.PRIVATE_WRITE})


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


class Verdict(StrEnum):
    ALLOW = "allow"
    ASK_USER = "ask_user"
    DENY = "deny"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"  # a dependency failed or was cancelled


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}
)


class GoalStatus(StrEnum):
    PLANNING = "planning"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    AVATAR_STATE = "avatar.state"
    MODEL_START = "model.start"
    MODEL_END = "model.end"
    TOOL_START = "tool.start"
    TOOL_END = "tool.end"
    SENTINEL_VERDICT = "sentinel.verdict"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    TASK_STATUS = "task.status"
    GOAL_STATUS = "goal.status"
    RUN_START = "run.start"
    RUN_END = "run.end"
    CHAT_DELTA = "chat.delta"
    MEMORY_CHANGED = "memory.changed"
    RULE_CHANGED = "rule.changed"
