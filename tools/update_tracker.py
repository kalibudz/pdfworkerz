"""Evidence gate between test results and tracker/features.json.

pytest (via tests/conftest.py) writes build/feature_results.json, mapping each
feature id to per-criterion pass/fail counts collected from tests marked
``@pytest.mark.feature("ABC-01")``.

    python tools/update_tracker.py --write   # sync statuses from evidence
    python tools/update_tracker.py --check   # fail if any "done" claim lacks evidence

A feature is "done" only when every acceptance criterion (or criterion 1 if the
feature lists none) has at least one passing test and no linked test fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import features

Results = dict[str, dict[str, dict[str, int]]]


def load_results(path: Path | None = None) -> Results:
    path = path or features.RESULTS_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data: Results = json.load(fh)
    return data


def evidence_status(feat: dict[str, Any], results: Results) -> str | None:
    """Status proven by tests: 'done', 'failing', 'in-progress', or None when untested."""
    per_criterion = results.get(feat["id"])
    if not per_criterion:
        return None
    if any(c.get("failed", 0) > 0 for c in per_criterion.values()):
        return "failing"
    needed = len(feat.get("criteria") or [None])
    covered = all(per_criterion.get(str(n), {}).get("passed", 0) > 0 for n in range(1, needed + 1))
    return "done" if covered else "in-progress"


def sync(data: dict[str, Any], results: Results) -> list[str]:
    """Update statuses in place from evidence; return a log of changes."""
    changes: list[str] = []
    for feat in data["features"]:
        proven = evidence_status(feat, results)
        old = feat["status"]
        if proven is not None:
            new = proven
        elif old in ("done", "failing"):
            new = "planned"  # a claim with no evidence behind it is withdrawn
        else:
            new = old
        if new != old:
            feat["status"] = new
            changes.append(f"{feat['id']}: {old} -> {new}")
    return changes


def check(data: dict[str, Any], results: Results) -> tuple[list[str], list[str]]:
    """Return (errors, notices). Errors are 'done' claims without passing evidence."""
    errors: list[str] = []
    notices: list[str] = []
    for feat in data["features"]:
        proven = evidence_status(feat, results)
        if feat["status"] == "done" and proven != "done":
            errors.append(f"{feat['id']} is marked done but evidence says {proven or 'untested'}")
        elif proven == "done" and feat["status"] != "done":
            notices.append(f"{feat['id']} has passing evidence; run --write to mark it done")
    return errors, notices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="update statuses in features.json")
    mode.add_argument("--check", action="store_true", help="exit 1 if any done claim lacks evidence")
    args = parser.parse_args(argv)

    data = features.load()
    problems = features.validate(data)
    if problems:
        print("features.json is invalid:", *problems, sep="\n  ")
        return 1
    results = load_results()

    if args.write:
        changes = sync(data, results)
        features.save(data)
        print("\n".join(changes) if changes else "No status changes.")
        return 0

    errors, notices = check(data, results)
    for line in notices:
        print(f"notice: {line}")
    for line in errors:
        print(f"error: {line}")
    done = sum(f["status"] == "done" for f in data["features"])
    print(f"{done}/{len(data['features'])} features done; {len(errors)} unsupported claims.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
