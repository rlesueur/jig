"""Plain-English explanations of the errors people meet when starting and setting up Jig.

``explain(exc)`` returns a short title and a sentence or two saying what to do, for the terminal, the
set-up page and Settings. The technical message stays in the log (and under "Details" in the UI).
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .endpoints import PROVIDERS, classify
from .errors import (ConfigError, ModelCapabilityError, ModelError, ModelKeyMissing, ModelServerUnavailable,
                     SandboxUnavailable, VaultUnavailable)

# The usual ports of the model apps Jig works with, and how to start each one's server.
MODEL_APPS = {
    8080: ("llama.cpp", "start llama-server"),
    1234: ("LM Studio", "open LM Studio, go to the Developer tab and start the server"),
    11434: ("Ollama", "open the Ollama app (or run ollama serve)"),
}
APPS_SENTENCE = ("llama.cpp usually uses port 8080, LM Studio 1234 (start its server in the Developer tab) and "
                 "Ollama 11434.")

CHECKS = {
    "tools": ("It didn't use tools properly, and Jig needs that to do anything for you. Choose a model that "
              "supports tool calling. With llama.cpp, start llama-server with --jinja."),
    "structured": ("It couldn't answer in the exact format Jig asks for (JSON), which Jig needs to plan tasks. "
                   "Choose a model that supports structured output."),
    "sentinel": ("The safety checker's model couldn't answer in the exact format Jig asks for (JSON), so Jig "
                 "can't check actions safely with it."),
    "vision": ("It couldn't describe a test picture, so it can't see images. Turn pictures off, or choose a model "
               "that can see them (with llama.cpp, start it with its --mmproj file)."),
}


def _k(tokens: int) -> str:
    return f"{tokens // 1024}K" if tokens >= 1024 else str(tokens)


def _context_fix(app: str | None) -> str:
    if app == "ollama":
        fix = "In the Ollama app, open Settings and set Context length to 32k, then choose Try again."
        if value := os.environ.get("OLLAMA_CONTEXT_LENGTH"):
            fix += (f" (This computer also has OLLAMA_CONTEXT_LENGTH set to {value}. The app's setting overrides "
                    "it, but if you start Ollama with 'ollama serve' instead, change that to 32768.)")
        return fix
    if app == "lmstudio":
        return ("In LM Studio, eject the model and load it again with Context Length set to 32768, then choose Try "
                "again. Or pick a model LM Studio hasn't loaded yet: Jig loads it with 32K for you.")
    if app == "llamacpp":
        return "Start llama-server again with -c 32768, then choose Try again."
    return "Set the context length in your model app to 32768, then choose Try again."


def context_too_small(tokens: int, app: str | None, floor: int) -> str:
    name = {"ollama": "Ollama", "lmstudio": "LM Studio", "llamacpp": "llama.cpp"}.get(app or "", "Your model app")
    return (f"{name} gave the model a {_k(tokens)} context: that's how much of the conversation, Jig's instructions "
            f"and what it reads it can hold at once. Those come to about 6K on the first web page, so it would fail "
            f"part-way through. Jig needs at least {_k(floor)}, and 32K is best. {_context_fix(app)}")


def context_note(tokens: int | None, app: str | None, advised: int,
                 reload_context: int | str | None = None) -> str | None:
    """A warning for a context that passes the check but is smaller than advised, or for LM Studio set to reload
    models with less than advised (``reload_context``, its Default Context Length), or None."""
    if tokens and tokens < advised:
        return (f"It has a {_k(tokens)} context, which is enough to start, but Jig works best with {_k(advised)}, or "
                f"it may lose track of longer tasks. {_context_fix(app).replace(', then choose Try again', '')}")
    if app == "lmstudio" and isinstance(reload_context, int) and reload_context < advised:
        return (f"One more thing for LM Studio: when a model hasn't been used for a while, LM Studio puts it away, and "
                f"when Jig next needs it, LM Studio loads it again with its own Default Context Length, which is "
                f"{_k(reload_context)} on this computer. That's too small for longer tasks. To keep {_k(advised)}, "
                f"open LM Studio, go to Settings, then Model Defaults, and set Default Context Length to a custom "
                f"value of {advised}.")
    return None


@dataclass(frozen=True)
class Explained:
    title: str
    text: str
    kind: str  # what the UI should offer: "model", "key", "consent", "capability", "sandbox", "other"

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "text": self.text, "kind": self.kind}

    def __str__(self) -> str:
        return f"{self.title} {self.text}"


def _port(url: str) -> int | None:
    try:
        parts = urlsplit(url)
        return parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    except ValueError:
        return None


def _provider_for(url: str) -> Any:
    try:
        host = classify(url).host
    except ValueError:
        return None
    return next((p for p in PROVIDERS.values() if p.host == host), None)


def _url_in(exc: ModelServerUnavailable, config: Any) -> str:
    text = str(exc)
    if config is not None:
        for ep in (config.model, config.sentinel):
            if ep.base_url and ep.base_url in text:
                return ep.base_url
        return config.model.base_url
    return ""


def model_unreachable(url: str) -> str:
    if url and classify(url).is_cloud:
        return f"Jig couldn't reach {classify(url).host}. Check this computer is online."
    port = _port(url)
    if port in MODEL_APPS:
        app, how = MODEL_APPS[port]
        return (f"Jig couldn't reach a model at {url}. That's {app}'s usual port: {how}, then try again. "
                f"If you use a different app, {APPS_SENTENCE}")
    return f"Jig couldn't reach a model at {url}. Is your model app running? {APPS_SENTENCE}"


def explain(exc: BaseException, config: Any = None) -> Explained:
    from .cloud import CloudConsentRequired
    from .instance import InstanceLocked

    if isinstance(exc, ModelServerUnavailable):
        url = _url_in(exc, config)
        reason = getattr(exc, "reason", "")
        if reason == "key_refused":
            provider = _provider_for(url)
            name = provider.label if provider else "The model service"
            where = f" Copy it again from {provider.key_url}" if provider and provider.key_url else " Copy it again"
            return Explained("The API key wasn't accepted.",
                             f"{name} didn't accept the API key.{where}, then paste it into Jig again.", "key")
        if reason == "not_served":
            have = ", ".join(exc.available) or "none"
            return Explained("That model isn't loaded.",
                             f"The model app at {url} doesn't have the model Jig is set to use. It has: {have}. Load "
                             "it in your model app, or choose one of those in Jig's model settings.", "model")
        if reason == "which_model":
            return Explained("Which model should Jig use?",
                             f"{url} has several models loaded ({', '.join(exc.available)}). Choose one in "
                             "Jig's model settings.", "model")
        if reason in ("http_error", "not_json"):
            status = f" (error {exc.status})" if getattr(exc, "status", None) else ""
            return Explained("The model app answered with an error.",
                             f"{url} answered, but not like a model server{status}. Check the address in Jig's "
                             "model settings.", "model")
        if reason == "unreachable" or not reason and "unreachable" in str(exc):
            return Explained("Jig couldn't reach your model.", model_unreachable(url), "model")
        return Explained("Your model isn't ready.", str(exc), "model")
    if isinstance(exc, ModelKeyMissing):
        short = exc.secret.split(".", 1)[-1]
        provider = PROVIDERS.get(short)
        name = provider.label if provider else short
        return Explained("No API key yet.", f"Jig is set to use {name}, but no API key is saved for it. Paste one "
                         "into Jig's model settings.", "key")
    if isinstance(exc, CloudConsentRequired):
        return Explained("Jig needs your OK first.",
                         "Jig is set to use a cloud model, which means what you ask it is sent over the internet. "
                         "It won't send anything until you confirm in Jig's model settings.", "consent")
    if isinstance(exc, ModelCapabilityError):
        check = getattr(exc, "check", "")
        if check == "context" and getattr(exc, "context", None):
            return Explained("The model's context is too small.",
                             context_too_small(exc.context, getattr(exc, "app", None), exc.floor), "capability")
        return Explained("This model can't do everything Jig needs.",
                         CHECKS.get(check, "It failed one of Jig's checks."), "capability")
    if isinstance(exc, ModelError):
        return Explained("The model gave an error.", str(exc), "model")
    if isinstance(exc, SandboxUnavailable):
        return Explained("Running code needs Docker.",
                         "Jig is set to run code in a safe container, but Docker isn't available. Start Docker "
                         "Desktop and try again, or turn off running code in Jig's model settings.", "sandbox")
    if isinstance(exc, InstanceLocked):
        return Explained("Jig is already running.", str(exc), "other")
    if isinstance(exc, VaultUnavailable):
        return Explained("Jig couldn't open its safe store for keys.", str(exc), "other")
    if isinstance(exc, ConfigError):
        return Explained("There's a problem with Jig's settings.", str(exc), "other")
    if isinstance(exc, OSError) and (exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048):
        return Explained("That port is in use.", "Another program is already using Jig's port. Close it, or choose "
                         "another port with --port.", "other")
    return Explained("Something went wrong.", str(exc) or type(exc).__name__, "other")
