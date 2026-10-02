"""The data folder's ``settings.toml``: what the set-up page and Settings chose (see ``config.SETTINGS_FILE``).

``sections_for(choice)`` turns a choice from the web UI into config sections, starting from the profile for
that model app (``profiles/``) so its tested settings come along. ``save(data_dir, sections)`` replaces those
sections in the file and keeps the rest. jig.toml is never rewritten.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, SETTINGS_FILE, SETTINGS_SECTIONS
from .endpoints import PROVIDERS
from .errors import ConfigError

PROFILES_DIR = DEFAULT_CONFIG_PATH.parent / "profiles"
# The template [model] for each local app: its profile, or for llama.cpp the shipped jig.toml.
LOCAL_TEMPLATES = {"llamacpp": DEFAULT_CONFIG_PATH, "lmstudio": PROFILES_DIR / "lmstudio.toml",
                   "ollama": PROFILES_DIR / "ollama.toml", "other": None}


def _read(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8-sig"))


def sections_for(choice: dict[str, Any]) -> dict[str, dict[str, Any]]:
    kind = choice.get("kind")
    if kind == "local":
        app = choice.get("app") or "other"
        if app not in LOCAL_TEMPLATES:
            raise ConfigError(f"unknown model app {app!r}")
        base_url = str(choice.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            raise ConfigError("choose a model server first")
        template = LOCAL_TEMPLATES[app]
        model = dict(_read(template).get("model", {})) if template and template.is_file() else {}
        model.pop("launch", None)
        model.update(base_url=base_url, name=str(choice.get("name") or "").strip())
        return {"model": model, "sentinel": {}, "vision": {"enabled": bool(choice.get("vision"))}}
    if kind == "cloud":
        provider = choice.get("provider")
        if provider not in PROVIDERS:
            raise ConfigError(f"unknown cloud provider {provider!r}; choose one of {sorted(PROVIDERS)}")
        profile = _read(PROFILES_DIR / f"{provider}.toml")
        model = dict(profile.get("model", {}))
        model.pop("launch", None)
        if name := str(choice.get("name") or "").strip():
            model["name"] = name
        return {"model": model, "sentinel": dict(profile.get("sentinel", {})), "vision": {"enabled": False}}
    raise ConfigError("kind must be 'local' or 'cloud'")


def cloud_default(provider: str) -> dict[str, Any]:
    profile = _read(PROFILES_DIR / f"{provider}.toml")
    return {"name": profile.get("model", {}).get("name", "")}


def load(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = Path(data_dir) / SETTINGS_FILE
    return _read(path) if path.is_file() else {}


def save(data_dir: Path, sections: dict[str, dict[str, Any]]) -> Path:
    if unknown := set(sections) - set(SETTINGS_SECTIONS):
        raise ConfigError(f"settings.toml doesn't hold {sorted(unknown)}")
    merged = {**load(data_dir), **sections}
    path = Path(data_dir) / SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(dumps(merged), encoding="utf-8")
    os.replace(tmp, path)
    return path


def dumps(sections: dict[str, dict[str, Any]]) -> str:
    lines = ["# Saved by Jig's set-up page and Settings. Each section here replaces the same section of jig.toml",
             "# ([sandbox] is merged into it). Delete this file to go back to jig.toml alone.", ""]
    for name, table in sections.items():
        _table(lines, name, table)
    return "\n".join(lines) + "\n"


def _table(lines: list[str], name: str, table: dict[str, Any]) -> None:
    lines.append(f"[{name}]")
    nested = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested.append((key, value))
        else:
            lines.append(f"{_key(key)} = {_value(value)}")
    lines.append("")
    for key, value in nested:
        _table(lines, f"{name}.{_key(key)}", value)


def _key(key: str) -> str:
    return key if key.replace("_", "").replace("-", "").isalnum() else json.dumps(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    raise ConfigError(f"can't save {value!r} in settings.toml")
