"""Shared names used across the runtime, the API and the avatar.

The avatar component (in ``avatar/``) renders the states in ``AvatarState``
and the variants in ``TaskVariant``. These are the avatar's canonical names.
Keep this module as the single source of truth so the two sides stay aligned.
The avatar also accepts the older aliases ``sleeping``, ``needs-approval`` and
``blocked``, but the runtime never emits them.
"""

from __future__ import annotations

from enum import StrEnum


class AvatarState(StrEnum):
    IDLE = "idle"
    MONITORING = "monitoring"  # active research schedules, with no research running
    THINKING = "thinking"
    WORKING = "working"  # always paired with a TaskVariant; may be flagged as background work
    TALKING = "talking"
    APPROVAL = "approval"
    SUCCESS = "success"
    ERROR = "error"
    PAUSED = "paused"  # the agent or a task is paused by the user


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
    MESSAGES = "messages"  # mail and chat in a connected account
    CALENDAR = "calendar"  # events in a connected calendar


CATEGORY_TO_VARIANT: dict[ToolCategory, TaskVariant] = {
    ToolCategory.WEB: TaskVariant.BROWSING,
    ToolCategory.FILES: TaskVariant.WRITING,
    ToolCategory.NOTES: TaskVariant.WRITING,
    ToolCategory.MEMORY: TaskVariant.WRITING,
    ToolCategory.CODE: TaskVariant.CODING,
    ToolCategory.COMMERCE: TaskVariant.SHOPPING,
    ToolCategory.TIME: TaskVariant.SCHEDULING,
    ToolCategory.CREDENTIALS: TaskVariant.WRITING,
    ToolCategory.MESSAGES: TaskVariant.WRITING,
    ToolCategory.CALENDAR: TaskVariant.SCHEDULING,
}

# Transient states (success / error) are shown for this long before the
# avatar settles back to whatever is still going on.
TRANSIENT_STATE_SECONDS = 3.0
# A tool call is often over in a moment, so the avatar keeps its working pose this long after the call ends
# (unless something else happens first: another tool, an answer, a question) instead of dropping straight
# back to thinking.
WORK_DWELL_SECONDS = 2.5


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
    PAUSED = "paused"  # paused by the user; resumes from its checkpoint
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


TERMINAL_GOAL_STATUSES = frozenset({GoalStatus.DONE, GoalStatus.FAILED, GoalStatus.CANCELLED})


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
    # What a file or code call did (files, command and exit status, tests, diff): for Jig's pages only.
    TOOL_SUMMARY = "tool.summary"
    # A browser action was recognised as a checkout, payment or booking (before review and approval).
    TOOL_CHECKOUT = "tool.checkout"
    SENTINEL_VERDICT = "sentinel.verdict"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    TASK_STATUS = "task.status"
    GOAL_STATUS = "goal.status"
    RUN_START = "run.start"
    RUN_END = "run.end"
    CHAT_DELTA = "chat.delta"
    MEMORY_CHANGED = "memory.changed"
    SCHEDULE_CHANGED = "schedule.changed"
    NOTE_CHANGED = "note.changed"
    HISTORY_CHANGED = "history.changed"  # conversations or job results were deleted
    RULE_CHANGED = "rule.changed"
    AGENT_STATUS = "agent.status"
    POWER_STOPPING = "power.stopping"
