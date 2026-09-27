"""Token look-ahead: decide whether the next task fits in the remaining session budget.

    python tools/session_budget.py --remaining 600000
    python tools/session_budget.py --remaining 600000 --reserve 0.25 --limit 5

Only tasks whose size estimate fits inside ``remaining * (1 - reserve)`` are
offered, in phase order, so a session never starts work it cannot finish.
See docs/SESSION_PROTOCOL.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import features

DEFAULT_RESERVE = 0.25
OPEN_STATUSES = ("in-progress", "failing", "planned")


def usable_budget(remaining: int, reserve: float = DEFAULT_RESERVE) -> int:
    if remaining < 0:
        raise ValueError("remaining must be >= 0")
    if not 0 <= reserve < 1:
        raise ValueError("reserve must be in [0, 1)")
    return int(remaining * (1 - reserve))


def can_start(estimate: int, remaining: int, reserve: float = DEFAULT_RESERVE) -> bool:
    return estimate <= usable_budget(remaining, reserve)


def next_tasks(data: dict[str, Any], remaining: int, reserve: float = DEFAULT_RESERVE) -> list[dict[str, Any]]:
    """Open features that fit the budget: unfinished work first, then by phase order."""
    sizes: dict[str, int] = data["sizes"]
    phase_rank = {p["id"]: i for i, p in enumerate(data["phases"])}
    budget = usable_budget(remaining, reserve)
    candidates = [f for f in data["features"] if f["status"] in OPEN_STATUSES and sizes[f["estTokens"]] <= budget]
    return sorted(
        candidates,
        key=lambda f: (f["status"] == "planned", phase_rank[f["phase"]], f["id"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remaining", type=int, required=True, help="tokens left in this session")
    parser.add_argument("--reserve", type=float, default=DEFAULT_RESERVE, help="fraction held back (default 0.25)")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    data = features.load()
    budget = usable_budget(args.remaining, args.reserve)
    tasks = next_tasks(data, args.remaining, args.reserve)
    print(f"Usable budget: {budget:,} tokens (reserve {args.reserve:.0%})")
    if not tasks:
        print("No open task fits. Write the checkpoint and end the session cleanly.")
        return 0
    for f in tasks[: args.limit]:
        size = data["sizes"][f["estTokens"]]
        print(f"  {f['id']:<7} {f['phase']}  {f['estTokens']} ~{size:>7,}  {f['status']:<11} {f['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
