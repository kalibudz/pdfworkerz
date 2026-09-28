"""Run every token-free review worker locally: the same checks .github/workflows/ci.yml defines (INF-03).

    python tools/gate.py                 # run every step, print a summary, exit 1 on any failure
    python tools/gate.py --job lint      # run only the named job(s); repeatable
    python tools/gate.py --list          # print the steps without running them

GitHub Actions is manual-only for this repo (Actions minutes are billed), so this
is the gate every change must pass before it is committed. Python tools run as
``sys.executable -m <tool>`` so an unrelated install on PATH can never stand in
for the project's pinned one.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # nosec B404 -- fixed argument lists only, never shell=True
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ("lint", "types", "spec-sync", "tests", "security", "tracker-build")


@dataclass(frozen=True)
class Step:
    job: str
    name: str
    cmd: tuple[str, ...]
    cwd: Path = ROOT


@dataclass(frozen=True)
class Outcome:
    step: Step
    ok: bool
    seconds: float


Runner = Callable[[Step], int]


def build_steps(python: str = sys.executable, npm: str | None = None) -> list[Step]:
    npm = npm or shutil.which("npm") or "npm"
    web, tracker = ROOT / "web", ROOT / "tracker"
    py_dirs = ("engine", "cli", "tools", "server")
    return [
        Step("lint", "ruff check", (python, "-m", "ruff", "check", ".")),
        Step("lint", "ruff format --check", (python, "-m", "ruff", "format", "--check", *py_dirs, "tests")),
        Step("types", "mypy (strict)", (python, "-m", "mypy", *py_dirs)),
        Step("spec-sync", "SPEC.md catalog", (python, "tools/gen_spec_catalog.py", "--check")),
        Step("spec-sync", "ops.schema.json", (python, "tools/gen_ops_schema.py", "--check")),
        Step("tests", "web: npm ci", (npm, "ci"), web),
        Step("tests", "web: npm run build", (npm, "run", "build"), web),
        Step(
            "tests",
            "pytest + coverage gate",
            (python, "-m", "pytest", "--feature-results", "--cov", "--cov-report=term-missing"),
        ),
        Step("tests", "evidence gate", (python, "tools/update_tracker.py", "--check")),
        Step("security", "bandit", (python, "-m", "bandit", "-q", "-r", *py_dirs)),
        Step("security", "pip-audit", (python, "-m", "pip_audit", "--skip-editable")),
        Step("security", "web: npm audit", (npm, "audit"), web),
        Step("tracker-build", "tracker: npm ci", (npm, "ci"), tracker),
        Step("tracker-build", "tracker: npm run build", (npm, "run", "build"), tracker),
    ]


def run_subprocess(step: Step) -> int:
    print(f"\n=== [{step.job}] {step.name}: {' '.join(step.cmd)}", flush=True)
    return subprocess.run(step.cmd, cwd=step.cwd, check=False).returncode  # nosec B603 -- fixed argv, no shell


def run(steps: Sequence[Step], runner: Runner = run_subprocess) -> list[Outcome]:
    outcomes = []
    for step in steps:
        start = time.monotonic()
        code = runner(step)
        outcomes.append(Outcome(step, code == 0, time.monotonic() - start))
    return outcomes


def summary(outcomes: Sequence[Outcome]) -> str:
    lines = ["", "Gate summary", f"{'job':<14} {'step':<26} {'result':<6} {'time':>8}"]
    for o in outcomes:
        lines.append(f"{o.step.job:<14} {o.step.name:<26} {'PASS' if o.ok else 'FAIL':<6} {o.seconds:>7.1f}s")
    failed = sum(not o.ok for o in outcomes)
    lines.append(f"{len(outcomes) - failed}/{len(outcomes)} steps passed" + (f"; {failed} FAILED" if failed else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None, runner: Runner = run_subprocess) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", action="append", choices=JOBS, help="run only this job (repeatable)")
    parser.add_argument("--list", action="store_true", help="print the steps without running them")
    args = parser.parse_args(argv)

    steps = [s for s in build_steps() if not args.job or s.job in args.job]
    if args.list:
        for s in steps:
            print(f"[{s.job}] {s.name}: {' '.join(s.cmd)}  (in {s.cwd.relative_to(ROOT)})")
        return 0

    outcomes = run(steps, runner)
    print(summary(outcomes))
    return 0 if all(o.ok for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
