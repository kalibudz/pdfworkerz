"""Load and validate tracker/features.json, the single source of truth for feature status."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FEATURES_PATH = ROOT / "tracker" / "features.json"
RESULTS_PATH = ROOT / "build" / "feature_results.json"

ID_PATTERN = re.compile(r"^[A-Z]{2,3}-\d{2}$")
PARITY_VALUES = {"iLovePDF", "Nitro", "Beyond"}
REQUIRED_KEYS = {"id", "category", "name", "parity", "libs", "phase", "estTokens", "status"}


def load(path: Path | None = None) -> dict[str, Any]:
    with (path or FEATURES_PATH).open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def dumps(data: dict[str, Any]) -> str:
    """Serialize with one list element per line so diffs show exactly which feature changed."""
    parts: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            items = ",\n".join("    " + json.dumps(v, ensure_ascii=False) for v in value)
            parts.append(f"  {json.dumps(key)}: [\n{items}\n  ]")
        else:
            parts.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def save(data: dict[str, Any], path: Path | None = None) -> None:
    (path or FEATURES_PATH).write_text(dumps(data), encoding="utf-8", newline="\n")


def validate(data: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []
    phases = {p["id"] for p in data.get("phases", [])}
    categories = {c["id"] for c in data.get("categories", [])}
    sizes = set(data.get("sizes", {}))
    statuses = set(data.get("statuses", []))
    seen: set[str] = set()

    for i, feat in enumerate(data.get("features", [])):
        where = feat.get("id", f"features[{i}]")
        missing = REQUIRED_KEYS - feat.keys()
        if missing:
            errors.append(f"{where}: missing keys {sorted(missing)}")
            continue
        fid = feat["id"]
        if not ID_PATTERN.match(fid):
            errors.append(f"{fid}: id must look like ABC-01")
        if fid in seen:
            errors.append(f"{fid}: duplicate id")
        seen.add(fid)
        if not fid.startswith(feat["category"] + "-"):
            errors.append(f"{fid}: id prefix does not match category {feat['category']}")
        if feat["category"] not in categories:
            errors.append(f"{fid}: unknown category {feat['category']}")
        if feat["phase"] not in phases:
            errors.append(f"{fid}: unknown phase {feat['phase']}")
        if feat["estTokens"] not in sizes:
            errors.append(f"{fid}: unknown size {feat['estTokens']}")
        if feat["status"] not in statuses:
            errors.append(f"{fid}: unknown status {feat['status']}")
        if not feat["parity"] or not set(feat["parity"]) <= PARITY_VALUES:
            errors.append(f"{fid}: parity must be a non-empty subset of {sorted(PARITY_VALUES)}")
        if not feat["libs"]:
            errors.append(f"{fid}: libs must name at least one real library")
        criteria = feat.get("criteria")
        if criteria is not None and (not isinstance(criteria, list) or not criteria):
            errors.append(f"{fid}: criteria must be a non-empty list when present")
    return errors
