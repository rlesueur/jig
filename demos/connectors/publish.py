"""Copy rendered connector takes to where the videos are collected, with an index of what each one shows.

    python demos/connectors/publish.py c-checkout c-checkout-howto ...

Each take's videos (with sound) go to the assets folder and promo/making-of/10-demos/, and takes.json there
records the capture, commit and capability result behind each file. Only takes whose capture passed are copied.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

DEMOS = Path(__file__).resolve().parent.parent
DESTS = [Path(r"C:\Users\you\.cursor\projects\c-Users-robyn-VideoAvatar\assets\jig-demos\connectors"),
         DEMOS.parent / "promo" / "making-of" / "10-demos"]


def main(takes: list[str]) -> None:
    for d in DESTS:
        d.mkdir(parents=True, exist_ok=True)
    for take in takes:
        scenario = (DEMOS / "scenarios" / f"{take}.mjs").read_text(encoding="utf-8")
        source = f"c-{take[2:].removesuffix('-howto')}" if take.endswith("-howto") else take
        stamp = (DEMOS / "captures" / source / "latest.txt").read_text(encoding="utf-8").strip()
        manifest = json.loads((DEMOS / "captures" / source / stamp / "manifest.json").read_text(encoding="utf-8"))
        if manifest["status"] != "passed":
            raise SystemExit(f"{source} {stamp} did not pass; not publishing {take}")
        kind = "instructional" if "instructional: true" in scenario else "promo"
        summary = next((n["text"] for n in manifest["notes"] if n["text"].startswith("== ")), "")
        for video in sorted((DEMOS / "out" / take).glob(f"jig-demo-{take}-*.mp4")):
            if video.stem.endswith("-silent"):
                continue
            size = video.stem.rsplit("-", 1)[-1]
            name = f"jig-connector-{take[2:].removesuffix('-howto')}-{kind}-{size}.mp4"
            for d in DESTS:
                shutil.copy2(video, d / name)
                index_file = d / "takes.json"
                index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
                index[name] = {"kind": kind, "format": size, "capture": f"{source}/{stamp}", "commit": manifest["commit"],
                               "capability": summary, "rendered_from": str(video)}
                index_file.write_text(json.dumps(index, indent=2), encoding="utf-8")
            print(f"{name}  <- {source}/{stamp} ({summary})")


if __name__ == "__main__":
    main(sys.argv[1:])
