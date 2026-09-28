from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402

REQUIRED_JOBS = {"lint", "types", "tests", "security", "spec-sync", "tracker-build"}
AI_MARKERS = ("anthropic", "openai", "api_key", "claude")
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


@pytest.mark.feature("INF-03")
def test_gate_defines_all_token_free_workers() -> None:
    assert {s.job for s in gate.build_steps()} >= REQUIRED_JOBS


@pytest.mark.feature("INF-03")
def test_gate_enforces_evidence_gate_and_spec_sync() -> None:
    commands = " ".join(" ".join(s.cmd) for s in gate.build_steps())
    assert "update_tracker.py --check" in commands
    assert "gen_spec_catalog.py --check" in commands
    assert "--feature-results" in commands


@pytest.mark.feature("INF-03")
def test_gate_keeps_going_after_a_failure_and_reports_it(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []

    def fake(step: gate.Step) -> int:
        seen.append(step.name)
        return 1 if step.name == "ruff check" else 0

    assert gate.main(["--job", "lint", "--job", "types"], runner=fake) == 1
    assert seen == ["ruff check", "ruff format --check", "mypy (strict)"]
    out = capsys.readouterr().out
    assert "2/3 steps passed; 1 FAILED" in out


@pytest.mark.feature("INF-03")
def test_gate_passes_when_every_step_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert gate.main(["--job", "spec-sync"], runner=lambda step: 0) == 0
    assert "2/2 steps passed" in capsys.readouterr().out


@pytest.mark.feature("INF-03")
def test_gate_list_runs_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    def never(step: gate.Step) -> int:
        raise AssertionError("--list must not run anything")

    assert gate.main(["--list"], runner=never) == 0
    assert "[tracker-build]" in capsys.readouterr().out


@pytest.mark.feature("INF-03")
def test_ci_is_manual_only_so_it_cannot_bill_unasked(workflow: dict) -> None:
    triggers = workflow.get("on") or workflow.get(True)  # PyYAML parses bare `on` as True
    assert set(triggers) == {"workflow_dispatch"}


@pytest.mark.feature("INF-03")
def test_manual_ci_still_mirrors_every_gate_job(workflow: dict) -> None:
    assert set(workflow["jobs"]) >= REQUIRED_JOBS


@pytest.mark.feature("INF-03")
@pytest.mark.parametrize("path", [ROOT / "tools" / "gate.py", CI_PATH], ids=["gate", "ci"])
def test_workers_use_no_ai_services(path: Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    for marker in AI_MARKERS:
        assert marker not in text, marker


@pytest.mark.feature("INF-02")
def test_tracker_component_reads_features_json() -> None:
    source = (ROOT / "tracker" / "src" / "FeatureTracker.jsx").read_text(encoding="utf-8")
    assert "features.json" in source
    assert "checkpoint.json" in source
    assert "export default function FeatureTracker" in source
