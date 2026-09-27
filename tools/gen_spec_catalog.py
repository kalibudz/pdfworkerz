"""Regenerate the feature tables in SPEC.md from tracker/features.json.

    python tools/gen_spec_catalog.py          # rewrite the generated sections
    python tools/gen_spec_catalog.py --check  # exit 1 if SPEC.md is stale

Generated sections sit between ``<!-- BEGIN GENERATED: name -->`` and
``<!-- END GENERATED: name -->`` markers; everything else in SPEC.md is prose.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import features

SPEC_PATH = features.ROOT / "SPEC.md"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_catalog(data: dict[str, Any]) -> str:
    out: list[str] = []
    for cat in data["categories"]:
        rows = [f for f in data["features"] if f["category"] == cat["id"]]
        out.append(f"#### {cat['id']} — {cat['name']} ({len(rows)})\n")
        out.append("| ID | Feature | Parity | Libraries | Phase | Size |")
        out.append("|---|---|---|---|---|---|")
        for f in rows:
            out.append(
                f"| {f['id']} | {_cell(f['name'])} | {', '.join(f['parity'])} | "
                f"{', '.join(f['libs'])} | {f['phase']} | {f['estTokens']} |"
            )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_parity(data: dict[str, Any]) -> str:
    total = len(data["features"])
    out = ["| Benchmark | Features covered | Feature IDs |", "|---|---|---|"]
    for tag, label in (("iLovePDF", "iLovePDF"), ("Nitro", "Nitro PDF Pro"), ("Beyond", "Beyond both")):
        ids = [f["id"] for f in data["features"] if tag in f["parity"]]
        out.append(f"| {label} | {len(ids)} | {', '.join(ids)} |")
    out.append(f"\nTotal features: **{total}**.")
    return "\n".join(out) + "\n"


def render_phases(data: dict[str, Any]) -> str:
    out = ["| Phase | Scope | Features | Est. tokens |", "|---|---|---|---|"]
    for p in data["phases"]:
        rows = [f for f in data["features"] if f["phase"] == p["id"]]
        tokens = sum(data["sizes"][f["estTokens"]] for f in rows)
        out.append(f"| {p['id']} | {p['name']} | {len(rows)} | ~{tokens / 1000:,.0f}k |")
    return "\n".join(out) + "\n"


RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "catalog": render_catalog,
    "parity": render_parity,
    "phases": render_phases,
}


def regenerate(spec: str, data: dict[str, Any]) -> str:
    for name, render in RENDERERS.items():
        pattern = re.compile(rf"(<!-- BEGIN GENERATED: {name} -->\n).*?(<!-- END GENERATED: {name} -->)", re.DOTALL)
        if not pattern.search(spec):
            raise ValueError(f"SPEC.md is missing the generated-section markers for '{name}'")
        body = render(data)

        def fill(m: re.Match[str], body: str = body) -> str:
            return m.group(1) + body + m.group(2)

        spec = pattern.sub(fill, spec)
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if SPEC.md is out of date")
    args = parser.parse_args(argv)

    data = features.load()
    problems = features.validate(data)
    if problems:
        print("features.json is invalid:", *problems, sep="\n  ")
        return 1
    current = SPEC_PATH.read_text(encoding="utf-8")
    updated = regenerate(current, data)
    if args.check:
        if current != updated:
            print("SPEC.md is stale: run python tools/gen_spec_catalog.py")
            return 1
        print("SPEC.md catalog is in sync with features.json.")
        return 0
    SPEC_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print("SPEC.md regenerated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
