"""Pick work back up after a session ended without warning.

    python tools/resume.py                 # what the last session was doing, and what to do now
    python tools/resume.py --check         # exit 1 if a task was left unfinished
    python tools/resume.py --begin "EDT-13 slice 2: align bar" --next "wire the align buttons"
    python tools/resume.py --progress "align buttons wired, tests pending" --next "write the tests"
    python tools/resume.py --end
    python tools/resume.py --hook          # the report as SessionStart hook JSON (.claude/settings.json)

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
import os
import subprocess  # nosec B404 -- fixed argument lists only, never shell=True
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
CHECKPOINT = REPO / "state" / "checkpoint.json"

STATUS_IN_PROGRESS = "in-progress"
STATUS_CLEAN = "clean"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
"""Claude Code sets this in every command it runs: which session is asking."""


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


def begin(
    task: str,
    path: Path | None = None,
    *,
    step: str | None = None,
    next_action: str | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    """Mark the checkpoint in-progress before the work starts.

    A new task replaces the old task's ``step`` and ``nextAction`` -- with what was given,
    or with an honest placeholder -- rather than leaving them in place. Found 2026-10-02:
    a ``--begin`` for P7 kept the finished selection fix's step and next action, so a
    session resuming from it would have been told to redo the wrong work."""
    data = load_checkpoint(path)
    data["status"] = STATUS_IN_PROGRESS
    data["startedAt"] = _now()
    data["updated"] = _now()[:10]
    data["ownerSession"] = current_session()
    if task:
        data["taskId"] = task
        data["step"] = step or "just started; no progress recorded yet"
        data["nextAction"] = next_action or f"carry on with: {task}"
    if phase:
        data["phase"] = phase
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        data["branch"] = branch
    save_checkpoint(data, path)
    return data


def progress(step: str, next_action: str | None = None, path: Path | None = None) -> dict[str, Any]:
    """Record how far the current task has got, at each natural break point, so an abrupt end
    loses at most the work since the last one. Leaves the in-progress mark as it is."""
    data = load_checkpoint(path)
    data["step"] = step
    if next_action:
        data["nextAction"] = next_action
    data["progressAt"] = _now()
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        data["branch"] = branch
    save_checkpoint(data, path)
    return data


def end(path: Path | None = None) -> dict[str, Any]:
    """Mark the checkpoint finished: the task is done, verified and committed."""
    data = load_checkpoint(path)
    data["status"] = STATUS_CLEAN
    data["startedAt"] = None
    data["ownerSession"] = None
    data["updated"] = _now()[:10]
    save_checkpoint(data, path)
    return data


def current_session() -> str | None:
    """The Claude Code session running this command, or None outside Claude Code."""
    return os.environ.get(SESSION_ENV) or None


def in_progress(data: dict[str, Any]) -> bool:
    """A task is marked started and not yet finished. A checkpoint with no ``status`` at all
    predates this tool, so it is read as clean -- an unfinished task then shows up as
    uncommitted work in the report instead."""
    return str(data.get("status", STATUS_CLEAN)) == STATUS_IN_PROGRESS


def owned_here(data: dict[str, Any], session: str | None = None) -> bool:
    """The task in progress belongs to the session asking: it is that session's own work, and
    still going (possibly after waiting out a usage limit, which keeps the session id)."""
    session = session or current_session()
    return bool(session) and data.get("ownerSession") == session


def was_interrupted(data: dict[str, Any], session: str | None = None) -> bool:
    """Whether a task was left unfinished by *another* session -- one that stopped mid-task.

    The task the asking session is itself working on is not an interruption, so the gate
    (whose first step is ``--check``) stays usable mid-task. Found 2026-10-02: before the
    owner was recorded, ``--check`` failed on a session's own in-progress mark, so the gate
    could never pass between ``--begin`` and ``--end``. Outside Claude Code (no session id)
    any in-progress mark counts, as before."""
    return in_progress(data) and not owned_here(data, session)


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
    elif in_progress(data):
        lines.append("Task in progress in this session (it is still going, not interrupted)")
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


def needs_attention(data: dict[str, Any], session: str | None = None) -> bool:
    """Whether a new session must deal with left-over work before anything else."""
    return was_interrupted(data, session) or bool(uncommitted()) or any(count for _, count in wip_branches())


def _hook_session() -> str | None:
    """The session id from the JSON Claude Code pipes to a hook, if there is any."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):  # unreadable or not JSON: a hook must still print its report
        return None
    session = payload.get("session_id") if isinstance(payload, dict) else None
    return str(session) if session else None


def hook_output(data: dict[str, Any] | None = None, session: str | None = None) -> dict[str, Any]:
    """What Claude Code's SessionStart hook prints: the resume report, injected into the new
    session's context, so resuming never depends on a session remembering to run this script.
    ``session`` is the id from the hook's own input: a session resumed or compacted mid-task
    is told to carry on with its own work rather than that it was cut off."""
    data = load_checkpoint() if data is None else data
    if owned_here(data, session) and in_progress(data):
        lead = (
            "PDFWorkerz: this session is part-way through the task below (it was resumed or its "
            "context was compacted). Carry on from step/nextAction; check the uncommitted files first."
        )
    elif needs_attention(data, session):
        lead = (
            "PDFWorkerz: the previous session did not finish its task (a usage limit, context "
            "overflow or crash cut it off). Before doing anything else -- including whatever the "
            "user just asked, unless they say otherwise -- read this report, check the "
            "uncommitted files and WIP branches it lists, run the gate, and resume the task at "
            "nextAction. Record progress with `python tools/resume.py --progress` at each break."
        )
    else:
        lead = "PDFWorkerz: the previous session ended cleanly. Its checkpoint, for reference:"
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": f"{lead}\n\n{report(data)}"}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--begin", metavar="TASK", help="mark the checkpoint in-progress before starting work")
    group.add_argument("--progress", metavar="STEP", help="record how far the current task has got")
    group.add_argument("--end", action="store_true", help="mark the checkpoint finished after committing")
    group.add_argument("--check", action="store_true", help="exit 1 if the last session was interrupted")
    group.add_argument("--hook", action="store_true", help="print the report as Claude Code SessionStart hook JSON")
    parser.add_argument("--step", help="with --begin: where the task starts")
    parser.add_argument("--next", dest="next_action", help="with --begin or --progress: the next concrete action")
    parser.add_argument("--phase", help="with --begin: the roadmap phase the task belongs to")
    args = parser.parse_args(argv)

    if args.begin is not None:
        begin(args.begin, step=args.step, next_action=args.next_action, phase=args.phase)
        print(f"checkpoint marked in-progress: {args.begin}")
        return 0
    if args.progress is not None:
        progress(args.progress, args.next_action)
        print(f"progress recorded: {args.progress}")
        return 0
    if args.hook:
        print(json.dumps(hook_output(session=_hook_session() or current_session())))
        return 0
    if args.end:
        end()
        print("checkpoint marked finished")
        return 0

    print(report())
    return 1 if (args.check and was_interrupted(load_checkpoint())) else 0


if __name__ == "__main__":
    raise SystemExit(main())
