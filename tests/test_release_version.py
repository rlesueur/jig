"""The release version is one PEP 440 string everywhere publish.ps1 and the container workflow compare it."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The same form publish.ps1 accepts: final X.Y.Z, or a PEP 440 pre-release (aN, bN, rcN). Not SemVer.
PEP440 = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")


def test_version_is_one_pep440_string():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert PEP440.fullmatch(version), version
    assert (ROOT / "jig" / "__init__.py").read_text(encoding="utf-8").splitlines()[2] == f'__version__ = "{version}"'
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    for image in ("jig", "jig-sandbox"):
        assert f"ghcr.io/rlesueur/{image}:{version}" in compose
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG VERSION={version}" in dockerfile
    workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")
    assert "type=raw,value=${{ needs.version.outputs.version }}" in workflow
    publish = (ROOT / "scripts" / "publish.ps1").read_text(encoding="utf-8")
    assert "X.Y.ZbN" in publish and "--prerelease" in publish
    # This first public release is a beta.
    assert version == "0.1.0b1"
    html = (ROOT / "jig" / "web" / "index.html").read_text(encoding="utf-8")
    assert 'class="beta-badge">Beta<' in html
