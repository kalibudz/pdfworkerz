"""Pick work back up after a session ended without warning.

    python tools/resume.py                 # what the last session was doing, and what to do now
    python tools/resume.py --check         # exit 1 if a task was left unfinished
    python tools/resume.py --begin "EDT-13 slice 2: align bar"
    python tools/resume.py --end

A usage limit, a context overflow or an expiry can stop a session between two tool
calls, with no chance to write a checkpoint. docs/SESSION_PROTOCOL.md section 4 covers
the tidy ending; this covers the other one.

``--begin`` marks the checkpoint ``in-progress`` *before* the work starts, so the mark
itself survives whatever happens next. ``--end`` clears it once the task is finished and
committed. A session that starts and finds the mark still set knows the session before it
was cut off, and this prints everything needed to carry on: the task, the step it had
reached, the branch, uncommitted files, WIP branches that are ahead, and the next action.

Nothing here writes to the repository except ``state/checkpoint.json``; recovering is
left to the session, which can see the report and decide.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 -- fixed argument lists only, never shell=True
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
CHECKPOINT = REPO / "state" / "checkpoint.json"

STATUS_IN_PROGRESS = "in-progress"
STATUS_CLEAN = "clean"


def _git(*args: str) -> str:
    """`git args...` in the repo, or "" when git is unavailable or the command fails."""
    try:
        done = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def load_checkpoint(path: Path | None = None) -> dict[str, Any]:
    """The checkpoint, or {} when it is missing or damaged. `path` defaults to
    CHECKPOINT as it is *now*: a default argument would bind the original file
    once, at import, and ignore any later change."""
    path = CHECKPOINT if path is None else path
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data


def save_checkpoint(data: dict[str, Any], path: Path | None = None) -> None:
    path = CHECKPOINT if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def begin(task: str, path: Path | None = None) -> dict[str, Any]:
    """Mark the checkpoint in-progress before the work starts."""
    data = load_checkpoint(path)
    data["status"] = STATUS_IN_PROGRESS
    data["startedAt"] = _now()
    data["updated"] = _now()[:10]
    if task:
        data["taskId"] = task
    save_checkpoint(data, path)
    return data


def end(path: Path | None = None) -> dict[str, Any]:
    """Mark the checkpoint finished: the task is done, verified and committed."""
    data = load_checkpoint(path)
    data["status"] = STATUS_CLEAN
    data["startedAt"] = None
    data["updated"] = _now()[:10]
    save_checkpoint(data, path)
    return data


def was_interrupted(data: dict[str, Any]) -> bool:
    """Whether the session before this one stopped in the middle of a task.

    A checkpoint with no ``status`` at all predates this tool, so it is read as clean --
    an unfinished task then shows up as uncommitted work in the report instead.
    """
    return str(data.get("status", STATUS_CLEAN)) == STATUS_IN_PROGRESS


def uncommitted() -> list[str]:
    status = _git("status", "--porcelain")
    return [line for line in status.splitlines() if line.strip()]


def wip_branches(main: str = "main") -> list[tuple[str, int]]:
    """Every `wip/...` branch, with how many commits it is ahead of `main`."""
    names = [line.strip() for line in _git("branch", "--list", "wip/*", "--format=%(refname:short)").splitlines()]
    ahead = []
    for name in [n for n in names if n]:
        count = _git("rev-list", "--count", f"{main}..{name}")
        ahead.append((name, int(count) if count.isdigit() else 0))
    return ahead


def report(data: dict[str, Any] | None = None) -> str:
    data = load_checkpoint() if data is None else data
    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "(unknown)"
    head = _git("log", "--oneline", "-1") or "(unknown)"
    lines: list[str] = []

    if was_interrupted(data):
        started = data.get("startedAt") or "an unrecorded time"
        lines.append(f"Previous session: INTERRUPTED (task marked in progress at {started}, never finished)")
    else:
        lines.append("Previous session: ended cleanly")

    for key in ("phase", "taskId", "step", "nextAction"):
        value = data.get(key)
        if value:
            lines.append(f"  {key:11} {value}")
    lines.append(f"  {'branch':11} {branch} (checkpoint says {data.get('branch', '?')})")
    lines.append(f"  {'head':11} {head}")
    if data.get("lastGreenCommit"):
        lines.append(f"  {'last green':11} {data['lastGreenCommit']}")

    changes = uncommitted()
    lines.append(f"Uncommitted files: {len(changes)}")
    lines.extend(f"  {line}" for line in changes[:20])
    if len(changes) > 20:
        lines.append(f"  ... and {len(changes) - 20} more")

    branches = [(name, count) for name, count in wip_branches() if count]
    if branches:
        lines.append("WIP branches ahead of main:")
        lines.extend(f"  {name} (+{count})" for name, count in branches)

    questions = data.get("openQuestions") or []
    if questions:
        lines.append(f"Open questions recorded: {len(questions)} (see state/checkpoint.json)")

    lines.append("")
    lines.append("Next:")
    lines.append("  1. python tools/gate.py        # the truth about the current state")
    if changes:
        lines.append("  2. review the uncommitted files above before changing anything")
    lines.append(f"  {'3' if changes else '2'}. resume: {data.get('nextAction') or '(no next action recorded)'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--begin", metavar="TASK", help="mark the checkpoint in-progress before starting work")
    group.add_argument("--end", action="store_true", help="mark the checkpoint finished after committing")
    group.add_argument("--check", action="store_true", help="exit 1 if the last session was interrupted")
    args = parser.parse_args(argv)

    if args.begin is not None:
        begin(args.begin)
        print(f"checkpoint marked in-progress: {args.begin}")
        return 0
    if args.end:
        end()
        print("checkpoint marked finished")
        return 0

    print(report())
    return 1 if (args.check and was_interrupted(load_checkpoint())) else 0


if __name__ == "__main__":
    raise SystemExit(main())
