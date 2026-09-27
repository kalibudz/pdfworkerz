"""Regenerate docs/ops.schema.json from the live Op registry (COR-04, SPEC.md section 9).

    python tools/gen_ops_schema.py          # rewrite docs/ops.schema.json
    python tools/gen_ops_schema.py --check  # exit 1 if it's out of date

Importing engine.ops.base registers the built-in Ops as a side effect;
concrete edit Ops (P2 onward) must be imported the same way before this
runs, or they won't appear in the schema.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.ops.base import json_schema_for_registry  # noqa: E402

SCHEMA_PATH = ROOT / "docs" / "ops.schema.json"


def render() -> str:
    return json.dumps(json_schema_for_registry(), indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if docs/ops.schema.json is out of date")
    args = parser.parse_args(argv)

    rendered = render()
    if args.check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else None
        if current != rendered:
            print("docs/ops.schema.json is stale: run python tools/gen_ops_schema.py")
            return 1
        print("docs/ops.schema.json is in sync with the Op registry.")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"docs/ops.schema.json regenerated ({SCHEMA_PATH})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
