from __future__ import annotations

import json
from pathlib import Path

import pytest

import session_budget

ROOT = Path(__file__).resolve().parents[2]

DATA = {
    "sizes": {"S": 50_000, "M": 150_000, "L": 400_000},
    "phases": [{"id": "P1"}, {"id": "P2"}],
    "features": [
        {"id": "A-02", "phase": "P2", "estTokens": "S", "status": "planned"},
        {"id": "A-01", "phase": "P1", "estTokens": "L", "status": "planned"},
        {"id": "B-01", "phase": "P2", "estTokens": "M", "status": "in-progress"},
        {"id": "C-01", "phase": "P1", "estTokens": "S", "status": "done"},
    ],
}


@pytest.mark.feature("INF-04")
def test_usable_budget_holds_back_reserve() -> None:
    assert session_budget.usable_budget(400_000, 0.25) == 300_000


@pytest.mark.feature("INF-04")
def test_can_start_boundary() -> None:
    assert session_budget.can_start(300_000, 400_000, 0.25)
    assert not session_budget.can_start(300_001, 400_000, 0.25)


@pytest.mark.feature("INF-04")
@pytest.mark.parametrize("remaining,reserve", [(-1, 0.25), (100, 1.0), (100, -0.1)])
def test_invalid_inputs_raise(remaining: int, reserve: float) -> None:
    with pytest.raises(ValueError):
        session_budget.usable_budget(remaining, reserve)


@pytest.mark.feature("INF-04")
def test_next_tasks_resumes_unfinished_work_first_and_skips_oversized() -> None:
    tasks = session_budget.next_tasks(DATA, remaining=300_000, reserve=0.25)  # usable 225k
    assert [t["id"] for t in tasks] == ["B-01", "A-02"]


@pytest.mark.feature("INF-04")
def test_next_tasks_empty_when_nothing_fits() -> None:
    assert session_budget.next_tasks(DATA, remaining=10_000) == []


@pytest.mark.feature("INF-04")
def test_cli_runs_against_real_features(capsys: pytest.CaptureFixture[str]) -> None:
    assert session_budget.main(["--remaining", "1000000", "--limit", "3"]) == 0
    assert "Usable budget: 750,000" in capsys.readouterr().out


@pytest.mark.feature("INF-04")
def test_checkpoint_file_has_required_fields() -> None:
    checkpoint = json.loads((ROOT / "state" / "checkpoint.json").read_text(encoding="utf-8"))
    for key in ("phase", "taskId", "step", "branch", "lastGreenCommit", "nextAction", "openQuestions", "updated"):
        assert key in checkpoint, key


@pytest.mark.feature("INF-04")
def test_session_protocol_documents_resume_procedure() -> None:
    text = (ROOT / "docs" / "SESSION_PROTOCOL.md").read_text(encoding="utf-8")
    for phrase in ("session_budget.py", "checkpoint.json", "usage_log.jsonl", "nextAction"):
        assert phrase in text, phrase
