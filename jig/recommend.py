"""Which local model to suggest for a graphics card's memory. Only measured figures are used.

Each entry is a real measurement (see README, "What you need"): the change in total GPU memory with the
model loaded in the upstream llama-server (``-ngl 99 --jinja --parallel 1``), on an RTX 5090. A card is
offered an entry when its memory covers the measurement plus ``HEADROOM_GB`` for Windows, the browser and
the driver. Anything not listed is unmeasured, and Jig says so rather than guessing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

HEADROOM_GB = 1.5
# Jig warns below this many tokens of context ([runtime] min_context_tokens in the shipped jig.toml).
ADVISED_CONTEXT = 32768


@dataclass(frozen=True)
class Measured:
    model: str
    quant: str
    file_gb: float
    context: int
    gpu_gb: float
    source: str


GRANITE = "Granite 4.2 8B"
MEASUREMENTS = (
    Measured(GRANITE, "Q4_K_M", 5.3, 32768, 10.6, "measured: llama-server, RTX 5090, change in total GPU memory"),
    Measured(GRANITE, "Q4_K_M", 5.3, 16384, 8.1, "measured: llama-server, RTX 5090, change in total GPU memory"),
)

# How to get the measured model in each app (the same Q4_K_M build of Granite 4.2 8B).
GET_IT = {
    "llamacpp": {
        "download": "https://huggingface.co/lmstudio-community/granite-4.2-8b-GGUF",
        "file": "granite-4.2-8b-Q4_K_M.gguf",
        "command": "llama-server -m granite-4.2-8b-Q4_K_M.gguf -ngl 99 --jinja -c {context} --port 8080",
    },
    "lmstudio": {
        "search": "lmstudio-community/granite-4.2-8b-GGUF",
        "file": "granite-4.2-8b-Q4_K_M.gguf",
        "steps": ["In LM Studio, search for granite-4.2-8b and download the Q4_K_M version from lmstudio-community.",
                  "Load it with the context length set to {context}.",
                  "Start the server in the Developer tab."],
    },
    "ollama": {
        "command": "ollama pull granite4.2:8b-q4_K_M",
        "steps": ["In the Ollama app, open Settings and set Context length to {context_k}.",
                  "Run: ollama pull granite4.2:8b-q4_K_M"],
    },
}


def recommend(gpu_total_gb: float | None) -> dict[str, Any]:
    """What to suggest for a card with ``gpu_total_gb`` of memory (None: no graphics card found)."""
    if gpu_total_gb is None:
        return {"fits": None, "measured": False,
                "text": "Jig didn't find a graphics card it can measure, so it has no measured suggestion for this "
                        "computer. You can use a model app you already have, or a cloud model."}
    for m in MEASUREMENTS:
        if gpu_total_gb >= m.gpu_gb + HEADROOM_GB:
            caveat = "" if m.context >= ADVISED_CONTEXT else (
                f" That's a {m.context // 1024}K context: Jig works best with {ADVISED_CONTEXT // 1024}K, which "
                f"measured {MEASUREMENTS[0].gpu_gb} GB, more than this card has to spare.")
            return {"fits": asdict(m), "measured": True, "get_it": _get_it(m.context),
                    "text": f"{m.model} ({m.quant}, a {m.file_gb} GB download) fits your {gpu_total_gb:.0f} GB card. "
                            f"With a {m.context // 1024}K context it used about {m.gpu_gb} GB when we measured it."
                            + caveat}
    smallest = MEASUREMENTS[-1]
    return {"fits": None, "measured": False,
            "text": f"Your card has {gpu_total_gb:.0f} GB. The smallest model Jig has measured, {smallest.model}, "
                    f"needs about {smallest.gpu_gb + HEADROOM_GB:.1f} GB with room to spare. Smaller models haven't "
                    "been measured with Jig, so a cloud model is the tested choice for this computer."}


def _get_it(context: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for app, info in GET_IT.items():
        out[app] = {k: ([s.format(context=context, context_k=f"{context // 1024}k") for s in v] if isinstance(v, list)
                        else v.format(context=context, context_k=f"{context // 1024}k")) for k, v in info.items()}
    return out
