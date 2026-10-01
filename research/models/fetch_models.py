"""Download the research model set from Hugging Face, with a disk-space guard and SHA-256 checks.

* Reads models.toml. For each model it asks the Hugging Face API for the file's size and full
  LFS SHA-256, and checks that the hash starts with the prefix recorded in models.toml.
* Refuses to start a download if the drive would drop below `min_free_gb` afterwards.
* Verifies every file (downloaded or already on disk) by hashing it locally.
* Writes models.lock.json with the full hash, size, path, licence and verification time.

Usage:  research\\.venv\\Scripts\\python research\\models\\fetch_models.py [--only KEY ...] [--verify-only]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download

HERE = Path(__file__).resolve().parent


def hf_file_meta(repo: str, filename: str) -> dict:
    r = httpx.get(f"https://huggingface.co/api/models/{repo}", params={"blobs": "true"}, timeout=60)
    r.raise_for_status()
    for s in r.json()["siblings"]:
        if s["rfilename"] == filename:
            return {"size": s["size"], "sha256": s["lfs"]["sha256"]}
    raise SystemExit(f"{filename} not found in {repo}")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(16 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    cfg = tomllib.loads((HERE / "models.toml").read_text(encoding="utf-8"))
    models_dir = Path(cfg["models_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    lock_path = HERE / "models.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    failed = False
    for key, m in cfg["models"].items():
        if args.only and key not in args.only:
            continue
        meta = hf_file_meta(m["repo"], m["file"])
        if not meta["sha256"].startswith(m["sha256"]):
            raise SystemExit(f"{key}: Hugging Face hash {meta['sha256']} does not match models.toml {m['sha256']}")
        path = Path(m["local_path"]) if m.get("local_path") else models_dir / m["file"]
        if not path.exists():
            if not m["download"] or args.verify_only:
                print(f"{key}: MISSING at {path}", file=sys.stderr)
                failed = True
                continue
            free = shutil.disk_usage(models_dir).free
            after_gb = (free - meta["size"]) / 2**30
            if after_gb < cfg["min_free_gb"]:
                raise SystemExit(f"{key}: refusing to download {meta['size'] / 2**30:.2f} GB; only {after_gb:.1f} GB "
                                 f"would remain on {models_dir.drive} (minimum {cfg['min_free_gb']} GB)")
            print(f"{key}: downloading {meta['size'] / 2**30:.2f} GB to {models_dir} "
                  f"({after_gb:.1f} GB will remain free)", flush=True)
            hf_hub_download(m["repo"], m["file"], local_dir=models_dir)
        print(f"{key}: hashing {path} ...", flush=True)
        digest = sha256_of(path)
        ok = digest == meta["sha256"]
        lock[key] = {"repo": m["repo"], "file": m["file"], "path": str(path), "size_bytes": path.stat().st_size,
                     "sha256": digest, "sha256_expected": meta["sha256"], "verified": ok, "licence": m["licence"],
                     "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        lock_path.write_text(json.dumps(lock, indent=1), encoding="utf-8")
        print(f"{key}: {'OK' if ok else 'CHECKSUM MISMATCH'} {digest}", flush=True)
        failed |= not ok
    print(f"free on {models_dir.drive}: {shutil.disk_usage(models_dir).free / 2**30:.1f} GB")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
