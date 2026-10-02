"""A model output cut off at max_tokens must surface as OutputCutOff carrying a diagnostic replay.

Live only (JIGBENCH_LIVE_8080=1): a real request to the owner's 8080 server through its API, with a tiny
output limit so the cut-off is genuine. It never starts, stops or reconfigures that server.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from jig.config import EndpointConfig
from jig.model import ModelClient
from jigbench.experiments.c1_memory import OutputCutOff, chat_with_cutoff_diagnostic


@pytest.mark.skipif(os.environ.get("JIGBENCH_LIVE_8080") != "1", reason="set JIGBENCH_LIVE_8080=1 to run")
def test_cutoff_carries_a_diagnostic_replay() -> None:
    async def go() -> str:
        model = ModelClient(EndpointConfig(base_url="http://127.0.0.1:8080/v1", name="bonsai-2-27b",
                                           max_tokens=24), label="agent model")
        await model.health()
        try:
            with pytest.raises(OutputCutOff) as info:
                await chat_with_cutoff_diagnostic(model, [{"role": "user", "content":
                                                           "List the first forty prime numbers, one per line."}])
        finally:
            await model.aclose()
        return str(info.value)

    text = asyncio.run(go())
    assert "cut off at max_tokens (24)" in text
    assert "Diagnostic replay (not scored)" in text and "reasoning" in text and "content" in text
