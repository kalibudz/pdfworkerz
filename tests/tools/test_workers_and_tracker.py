from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_JOBS = {"lint", "types", "tests", "security", "spec-sync", "tracker-build"}


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


@pytest.mark.feature("INF-03")
def test_ci_defines_all_token_free_workers(workflow: dict) -> None:
    assert set(workflow["jobs"]) >= REQUIRED_JOBS


@pytest.mark.feature("INF-03")
def test_ci_runs_on_push_and_pull_request(workflow: dict) -> None:
    triggers = workflow.get("on") or workflow.get(True)  # PyYAML parses bare `on` as True
    assert "push" in triggers and "pull_request" in triggers


@pytest.mark.feature("INF-03")
def test_ci_enforces_evidence_gate_and_spec_sync(workflow: dict) -> None:
    steps = " ".join(str(s.get("run", "")) for job in workflow["jobs"].values() for s in job["steps"])
    assert "update_tracker.py --check" in steps
    assert "gen_spec_catalog.py --check" in steps
    assert "--feature-results" in steps


@pytest.mark.feature("INF-03")
def test_ci_uses_no_ai_services(workflow: dict) -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").lower()
    for marker in ("anthropic", "openai", "api_key", "claude"):
        assert marker not in text, marker


@pytest.mark.feature("INF-02")
def test_tracker_component_reads_features_json() -> None:
    source = (ROOT / "tracker" / "src" / "FeatureTracker.jsx").read_text(encoding="utf-8")
    assert "features.json" in source
    assert "checkpoint.json" in source
    assert "export default function FeatureTracker" in source
