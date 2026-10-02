"""Which data-testids and ids do the tests and the demo pipeline use, and does the UI still have them?"""
import re
import sys
from pathlib import Path

ROOT = Path(r"jig")
USERS = [ROOT / "tests", ROOT / "demos" / "lib", ROOT / "demos" / "scenarios", ROOT / "demos" / "connectors", ROOT / "scripts"]
UI = [ROOT / "jig" / "web" / n for n in ("index.html", "app.js", "setup.js", "walkthrough.js", "autostart.js")]

pats = [r"get_by_test_id\(\s*[\"']([^\"']+)", r"data-testid=\\?[\"']([^\"'\\]+)", r"testid\(\s*[\"'`]([^\"'`$]+)",
        r"\[data-testid=\\?[\"']?([\w-]+)", r"getByTestId\(\s*[\"'`]([^\"'`$]+)"]
used: dict[str, set[str]] = {}
for base in USERS:
    for f in base.rglob("*"):
        if f.suffix not in (".py", ".mjs", ".js") or "node_modules" in f.parts or ".work" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for p in pats:
            for m in re.finditer(p, text):
                used.setdefault(m.group(1), set()).add(str(f.relative_to(ROOT)))
ui_text = "\n".join(p.read_text(encoding="utf-8") for p in UI)
missing = {k: v for k, v in used.items() if k not in ui_text}
print(f"{len(used)} test ids used")
if "-v" in sys.argv:
    for k in sorted(used):
        print(k, "<-", ", ".join(sorted(used[k]))[:160])
print("MISSING from UI:" if missing else "none missing")
for k, v in sorted(missing.items()):
    print(" ", k, "<-", ", ".join(sorted(v)))
