"""Fail if any committed notebook has outputs or execution counts (the repo is public:
outputs can contain account names, paths or tokens). Fix: nbstripout <file>."""
import json
import pathlib
import sys

bad = []
for path in pathlib.Path(".").rglob("*.ipynb"):
    if ".ipynb_checkpoints" in path.parts:
        continue
    nb = json.loads(path.read_text(encoding="utf-8"))
    if any(c.get("outputs") or c.get("execution_count") for c in nb.get("cells", [])):
        bad.append(str(path))
if bad:
    sys.exit("notebooks with outputs (run nbstripout on them): " + ", ".join(bad))
print("notebooks clean")
