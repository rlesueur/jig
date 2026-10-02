"""Keeps what people say to Jig out of the log files Jig writes (jig.log, and uvicorn's lines on the console
when Jig runs as a service).

Log lines name ids, paths, counts and exception types, never conversation content. Three things would
otherwise carry it in:

- web addresses: uvicorn logs every request's path *with* its query string, and httpx logs every
  outgoing request's URL at INFO (web searches and fetched pages included). Query strings are replaced
  with ``?[hidden]`` in uvicorn's lines, and httpx/httpcore only log warnings;
- exception messages: an error can quote a tool result, a model reply or a request. Tracebacks keep their
  frames and the exception's type, and say how long the message was instead of what it said;
- uncaught exceptions printed to stderr, which become log lines when there is no console: they go through
  logging, so the same rule applies.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import traceback
from types import TracebackType

HIDDEN_QUERY = "?[hidden]"
UVICORN_LOGGERS = ("uvicorn.access", "uvicorn.error")
QUIET_LIBRARIES = ("httpx", "httpcore")

_QUERY = re.compile(r"\?\S*")


def hide_query(text: str) -> str:
    """``/memory?q=walking HTTP/1.1`` -> ``/memory?[hidden] HTTP/1.1``."""
    return _QUERY.sub(HIDDEN_QUERY, text)


class HideQueryStrings(logging.Filter):
    """On uvicorn's loggers: the request path in access and WebSocket lines loses its query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(hide_query(a) if isinstance(a, str) and "?" in a else a for a in record.args)
        elif isinstance(record.msg, str) and "?" in record.msg and not record.args:
            record.msg = hide_query(record.msg)
        return True


def describe_exception(exc: BaseException) -> str:
    """``ValueError (83 characters)``: the type and the size of the message, not the message."""
    n = len(str(exc))
    return f"{type(exc).__qualname__} ({n} character{'' if n == 1 else 's'})" if n else type(exc).__qualname__


def format_exception(exc_type: type[BaseException] | None, exc: BaseException | None,
                     tb: TracebackType | None) -> str:
    """A traceback with its frames (file, line, function, source line) and each exception's type, chained
    causes included, but none of their messages."""
    if exc is None:
        return exc_type.__qualname__ if exc_type else ""
    chain: list[tuple[BaseException, str]] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    link = ""
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append((cur, link))
        if cur.__cause__ is not None:
            cur, link = cur.__cause__, "The above exception was the direct cause of the following exception:"
        elif cur.__context__ is not None and not cur.__suppress_context__:
            cur, link = cur.__context__, "During handling of the above exception, another exception occurred:"
        else:
            cur = None
    parts: list[str] = []
    for i, (e, _) in enumerate(reversed(chain)):
        if i:
            parts.append("\n" + chain[len(chain) - i][1] + "\n\n")
        parts.append("Traceback (most recent call last):\n")
        parts.extend(traceback.format_list(traceback.extract_tb(tb if e is exc else e.__traceback__)))
        parts.append(describe_exception(e) + "\n")
    return "".join(parts).rstrip("\n")


def content_free(handler: logging.Handler) -> logging.Handler:
    """Make ``handler`` format tracebacks with :func:`format_exception`."""
    formatter = handler.formatter or logging.Formatter()
    formatter.formatException = lambda ei: format_exception(*ei)  # type: ignore[method-assign]
    handler.setFormatter(formatter)
    return handler


def protect_logging() -> None:
    """Apply the rules above to every handler on the root and uvicorn loggers. Call it again after anything
    (uvicorn's own config, for example) adds handlers."""
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, HideQueryStrings) for f in logger.filters):
            logger.addFilter(HideQueryStrings())
    for name in ("", "uvicorn", *UVICORN_LOGGERS):
        for handler in logging.getLogger(name).handlers:
            content_free(handler)


# llama.cpp's server logs at verbosity 3 by default; above that (and with -v) it writes every prompt, reply
# and request body into its output, which Jig saves as model-server.log.
LLAMA_DEFAULT_VERBOSITY = 3
_LLAMA_VERBOSE = ("-v", "--verbose", "--log-verbose")
_LLAMA_VERBOSITY = ("-lv", "--verbosity", "--log-verbosity")


def verbose_model_server(args: list[str], env: dict[str, str]) -> str | None:
    """The launch setting that would make llama.cpp's server log conversations, or None."""
    for i, arg in enumerate(args):
        name, eq, inline = arg.partition("=")
        if name in _LLAMA_VERBOSE:
            return arg
        if name in _LLAMA_VERBOSITY:
            value = inline if eq else (args[i + 1] if i + 1 < len(args) else "")
            if not value.lstrip("-").isdigit() or int(value) > LLAMA_DEFAULT_VERBOSITY:
                return f"{name} {value}".strip()
    value = env.get("LLAMA_LOG_VERBOSITY", "")
    if value and (not value.lstrip("-").isdigit() or int(value) > LLAMA_DEFAULT_VERBOSITY):
        return f"LLAMA_LOG_VERBOSITY={value}"
    return None


def log_uncaught_exceptions() -> None:
    """Uncaught exceptions (main thread and other threads) are logged, so they follow the same rules,
    rather than printed to stderr."""
    crash = logging.getLogger("jig.crash")

    def main_hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        crash.error("uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        where = args.thread.name if args.thread else "a thread"
        crash.error("uncaught exception in %s", where, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = main_hook
    threading.excepthook = thread_hook
