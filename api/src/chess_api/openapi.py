"""The OpenAPI spec is the contract (the dashboard's TypeScript client is generated from it).

    uv run python -m chess_api.openapi              # write openapi.json
    uv run python -m chess_api.openapi --check      # exit 1 if openapi.json is out of date
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

from .app import create_app

DEFAULT = Path("openapi.json")  # run from api/


class _NoDb:
    def query(self, sql, params=()):  # the spec never touches Trino
        raise AssertionError("no queries while generating the spec")


def render() -> str:
    spec = create_app(db=_NoDb()).openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", nargs="?", type=Path, default=DEFAULT)
    p.add_argument("--check", action="store_true", help="compare instead of writing")
    args = p.parse_args(argv)
    spec = render()
    if not args.check:
        args.path.write_text(spec, encoding="utf-8", newline="\n")
        print(f"wrote {args.path}")
        return 0
    committed = args.path.read_text(encoding="utf-8") if args.path.exists() else ""
    if committed == spec:
        print(f"{args.path} is up to date")
        return 0
    sys.stdout.writelines(difflib.unified_diff(
        committed.splitlines(keepends=True), spec.splitlines(keepends=True),
        fromfile=f"{args.path} (committed)", tofile="generated"))
    print(f"\n{args.path} is out of date: run `uv run python -m chess_api.openapi` and commit it",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
