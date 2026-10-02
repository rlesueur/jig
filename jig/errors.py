"""Error types. Jig surfaces failures; it never substitutes fallback results."""

from __future__ import annotations


class JigError(Exception):
    """Base class for all Jig errors."""


class ConfigError(JigError):
    pass


class ModelServerUnavailable(JigError):
    """The local model server could not be reached or is not serving the model."""


class ModelCapabilityError(JigError):
    """The configured model cannot do something Jig requires (for example, well-formed tool calls)."""


class ModelError(JigError):
    """The model server returned an error or an unusable response."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


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
