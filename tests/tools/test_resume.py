"""INF-10: a session cut off mid-task must be detectable, and resumable, by the next one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import resume

ROOT = Path(__file__).resolve().parents[2]

CHECKPOINT = {
    "phase": "P6",
    "taskId": "FRM-01",
    "step": "writing the field extractor",
    "branch": "wip/FRM-01",
    "lastGreenCommit": "abc1234",
    "nextAction": "finish extract_fields() and its tests",
    "openQuestions": [],
    "updated": "2026-09-30",
}


@pytest.fixture
def checkpoint(tmp_path: Path) -> Path:
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(CHECKPOINT, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.feature("INF-10")
def test_begin_marks_in_progress_so_the_mark_survives_an_abrupt_end(checkpoint: Path) -> None:
    """The mark is written before the work, not after: a session killed between two tool
    calls never gets the chance to write anything."""
    assert resume.was_interrupted(resume.load_checkpoint(checkpoint)) is False  # no status yet: treated as clean

    resume.begin("FRM-02 slice 1", checkpoint)
    reloaded = resume.load_checkpoint(checkpoint)
    assert resume.was_interrupted(reloaded) is True
    assert reloaded["taskId"] == "FRM-02 slice 1"
    assert reloaded["startedAt"]
    assert reloaded["nextAction"] == CHECKPOINT["nextAction"]  # everything else is kept


@pytest.mark.feature("INF-10")
def test_end_clears_the_mark(checkpoint: Path) -> None:
    resume.begin("FRM-02", checkpoint)
    resume.end(checkpoint)
    data = resume.load_checkpoint(checkpoint)
    assert resume.was_interrupted(data) is False
    assert data["startedAt"] is None


@pytest.mark.feature("INF-10")
def test_a_damaged_or_missing_checkpoint_never_raises(tmp_path: Path) -> None:
    assert resume.load_checkpoint(tmp_path / "gone.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert resume.load_checkpoint(broken) == {}
    assert resume.was_interrupted({}) is False


@pytest.mark.feature("INF-10")
def test_the_report_says_what_to_resume_and_how(checkpoint: Path) -> None:
    interrupted = resume.begin("FRM-01", checkpoint)
    text = resume.report(interrupted)
    assert "INTERRUPTED" in text
    assert "finish extract_fields() and its tests" in text  # the next action, verbatim
    assert "tools/gate.py" in text  # the state is confirmed before anything is changed
    assert "Uncommitted files:" in text

    clean = resume.report(resume.end(checkpoint))
    assert "ended cleanly" in clean


@pytest.mark.feature("INF-10")
def test_check_exits_nonzero_only_after_an_interrupted_session(
    monkeypatch: pytest.MonkeyPatch, checkpoint: Path
) -> None:
    monkeypatch.setattr(resume, "CHECKPOINT", checkpoint)
    assert resume.main(["--begin", "FRM-01"]) == 0
    assert resume.main(["--check"]) == 1
    assert resume.main(["--end"]) == 0
    assert resume.main(["--check"]) == 0
    assert resume.main([]) == 0  # the plain report never fails


@pytest.mark.feature("INF-10")
def test_the_repositorys_own_checkpoint_carries_the_fields_a_cold_start_needs() -> None:
    data = resume.load_checkpoint(ROOT / "state" / "checkpoint.json")
    for field in ("phase", "taskId", "step", "branch", "lastGreenCommit", "nextAction", "status"):
        assert data.get(field) is not None, field


@pytest.mark.feature("INF-10")
def test_the_gate_checks_for_an_interrupted_session_before_anything_else() -> None:
    """`python tools/resume.py` on its own is only advisory -- nothing makes a session
    run it. Found by the owner: a session was cut off mid-task, and several more
    sessions' worth of unrelated work went by with the abandoned, uncommitted work
    sitting untouched, because nothing ever re-checked. The gate is the one thing
    every session runs before every commit, so that is where this has to be
    enforced, as its very first step -- not buried after lint/types/tests have
    already spent minutes on a task nobody should be starting yet."""
    import gate

    steps = gate.build_steps()
    assert steps[0].job == "session"
    assert steps[0].cmd[-2:] == ("tools/resume.py", "--check")
