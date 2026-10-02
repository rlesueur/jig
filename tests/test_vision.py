"""Vision: real image requests against the configured model server. Nothing is mocked.

The shipped jig.toml leaves [vision] off, because most models can't see pictures, so these tests send images
straight to the configured model: it must be vision-capable (the tested set-up, llama.cpp with --mmproj, is)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from jig.config import VisionConfig, load_config
from jig.errors import VisionUnavailable
from jig.model import ModelClient
from jig.vision import VisionService, image_message, probe_vision

MASCOT = Path(__file__).resolve().parent.parent / "avatar" / "assets" / "jig-mascot-reference.jpg"


@pytest.fixture
async def client(config):
    c = ModelClient(config.model, label="agent model")
    await c.health()
    try:
        yield c
    finally:
        await c.aclose()


async def test_vision_probe_sees_the_colour(client):
    result = await probe_vision(client)
    assert result["vision"] is True
    assert result["probe_colour"] in result["answer"].lower()


async def test_real_photo_is_described(client):
    result = await client.chat([image_message("What is shown in this image? Answer in one sentence.",
                                              [MASCOT.read_bytes()])])
    text = result.content.lower()
    assert any(w in text for w in ("creature", "character", "mascot", "figure", "animal", "robot", "monster")), text


async def test_tools_fail_loudly_when_vision_is_disabled(config):
    off = dataclasses.replace(config, vision=VisionConfig(enabled=False))
    c = ModelClient(off.model, label="agent model")
    try:
        service = VisionService(c, off.vision)
        with pytest.raises(VisionUnavailable, match="--mmproj"):
            await service.describe(MASCOT.read_bytes(), "What is this?")
    finally:
        await c.aclose()


def test_vision_flag_is_read_from_config(tmp_path):
    cfg = tmp_path / "jig.toml"
    cfg.write_text('[model]\nbase_url = "http://127.0.0.1:8080/v1"\n[vision]\nenabled = true\n', encoding="utf-8")
    assert load_config(cfg).vision.enabled is True
