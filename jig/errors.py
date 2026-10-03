"""Error types. Jig surfaces failures; it never substitutes fallback results."""

from __future__ import annotations

from typing import Any


class JigError(Exception):
    """Base class for all Jig errors."""


class ConfigError(JigError):
    pass


class ModelServerUnavailable(JigError):
    """The local model server could not be reached or is not serving the model.

    ``reason`` says which, for plain-English messages (``jig.friendly``): "unreachable", "key_refused",
    "http_error", "not_json", "not_served" or "which_model" (several models and no name set); empty when
    not known. ``available`` lists the models the server reported, ``status`` its HTTP status."""

    def __init__(self, message: str, *, reason: str = "", status: int | None = None,
                 available: list[str] | None = None):
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.available = available or []


class ModelKeyMissing(ConfigError):
    """An endpoint names a vault key (``api_key_secret``) that has not been stored yet."""

    def __init__(self, message: str, *, secret: str):
        super().__init__(message)
        self.secret = secret


class ModelCapabilityError(JigError):
    """The configured model cannot do something Jig requires (for example, well-formed tool calls).
    ``check`` names the check it failed ("tools", "structured", "sentinel" or "vision") when known."""

    check: str = ""


class ModelError(JigError):
    """The model server returned an error or an unusable response. ``record`` holds content-free details for the
    run step (for a structured answer: why each attempt was refused, and the stops and retries)."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None,
                 record: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.record = record or {}


class ModelStopped(ModelError):
    """Jig stopped a reply as it streamed because it was not making progress (``jig.progress``): it was
    repeating itself. ``stop`` is the content-free ``jig.progress.Stop``; ``partial`` the ``ChatResult`` of what
    had arrived, without any tool call that was cut off."""

    def __init__(self, message: str, *, stop: Any, partial: Any):
        super().__init__(message, record={"stop": stop.record()})
        self.stop = stop
        self.partial = partial


class ModelStalled(ModelError):
    """The model server sent nothing for longer than the liveness timeout, so Jig stopped waiting."""


class RepeatedActions(JigError):
    """A run made the same tool call and got the same result several times, so it was stopped."""


class ToolError(JigError):
    """A tool failed while executing."""


class ToolNotFound(JigError):
    pass


class ToolArgumentError(JigError):
    pass


class ModeViolation(JigError):
    """A tool was called that the current mode does not permit."""


class PolicyBlocked(JigError):
    """A core rule, custom rule or the Sentinel blocked an action."""


class ApprovalDenied(JigError):
    pass


class SentinelError(JigError):
    """The Sentinel could not produce a valid verdict. The action does not proceed."""


class SandboxViolation(JigError):
    pass


class SandboxUnavailable(JigError):
    """The configured sandbox backend (for example Docker) cannot be used. Jig does not fall back."""


class VisionUnavailable(JigError):
    """A tool needs image input but vision is disabled or the model failed the vision check."""


class VaultUnavailable(JigError):
    pass


class SecretNotFound(JigError):
    pass


class NotFound(JigError):
    pass


class CannotDelete(JigError):
    """A conversation or job can't be deleted yet: it is still running, or unfinished work needs it."""


class StepLimitExceeded(JigError):
    pass


class ConnectorError(JigError):
    """A connected account (Gmail and so on) refused or failed a request. Never retried as something else."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status  # the provider's HTTP status, when it answered


class ConnectorNotConnected(ConnectorError):
    """The account is not connected, or not with the access this tool needs."""


class ConnectorAuthError(ConnectorError):
    """The provider rejected Jig's grant (expired, revoked or missing scopes); the user has to reconnect."""
