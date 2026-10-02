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


@pytest.fixture(autouse=True)
def _no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests often run inside a Claude Code session, which sets CLAUDE_CODE_SESSION_ID;
    each test chooses its own session (or none) instead of inheriting that one."""
    monkeypatch.delenv(resume.SESSION_ENV, raising=False)


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
    assert reloaded["lastGreenCommit"] == CHECKPOINT["lastGreenCommit"]  # what isn't about the task is kept


@pytest.mark.feature("INF-10")
def test_a_new_task_never_inherits_the_previous_tasks_step_or_next_action(checkpoint: Path) -> None:
    """Found 2026-10-02: --begin for P7 kept the finished selection fix's step and next
    action, so a resumed session would have been sent back to redo the old task."""
    data = resume.begin("FRM-02", checkpoint)
    assert CHECKPOINT["step"] not in data["step"] and CHECKPOINT["nextAction"] not in data["nextAction"]
    assert "FRM-02" in data["nextAction"]
    given = resume.begin("FRM-03", checkpoint, step="reading the spec", next_action="write the parser", phase="P6")
    assert (given["step"], given["nextAction"], given["phase"]) == ("reading the spec", "write the parser", "P6")


@pytest.mark.feature("INF-10")
def test_only_another_sessions_unfinished_task_counts_as_interrupted(
    monkeypatch: pytest.MonkeyPatch, checkpoint: Path
) -> None:
    """Found 2026-10-02: `--check` (the gate's first step) failed on the asking session's own
    in-progress mark, so the gate could never pass between --begin and --end. The owner is
    recorded now: a session's own task -- including after it waits out a usage limit, which
    keeps its id -- is not an interruption; a different session's is."""
    monkeypatch.setenv(resume.SESSION_ENV, "session-A")
    data = resume.begin("FRM-02", checkpoint)
    assert data["ownerSession"] == "session-A"
    assert resume.was_interrupted(data) is False
    monkeypatch.setattr(resume, "CHECKPOINT", checkpoint)
    assert resume.main(["--check"]) == 0, "the gate passes for the session doing the work"
    assert "in progress in this session" in resume.report(data)

    monkeypatch.setenv(resume.SESSION_ENV, "session-B")
    assert resume.was_interrupted(data) is True
    assert resume.main(["--check"]) == 1, "a new session is stopped until it deals with the old task"

    monkeypatch.delenv(resume.SESSION_ENV)
    assert resume.was_interrupted(data) is True, "outside Claude Code any open mark counts"
    assert resume.end(checkpoint)["ownerSession"] is None


@pytest.mark.feature("INF-10")
def test_a_session_resumed_mid_task_is_told_to_carry_on_not_that_it_was_cut_off(checkpoint: Path) -> None:
    data = resume.begin("FRM-02", checkpoint, next_action="write the parser")
    data["ownerSession"] = "session-A"
    context = resume.hook_output(data, session="session-A")["hookSpecificOutput"]["additionalContext"]
    assert "part-way through" in context and "write the parser" in context


@pytest.mark.feature("INF-10")
def test_the_hook_reads_the_session_id_claude_code_pipes_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "abc", "source": "startup"}'))
    assert resume._hook_session() == "abc"
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert resume._hook_session() is None


@pytest.mark.feature("INF-10")
def test_progress_records_where_the_task_is_without_ending_it(checkpoint: Path) -> None:
    resume.begin("FRM-02", checkpoint)
    data = resume.progress("parser written, tests next", "write the parser tests", checkpoint)
    assert resume.was_interrupted(data), "still in progress: only --end clears the mark"
    assert data["step"] == "parser written, tests next" and data["nextAction"] == "write the parser tests"
    assert data["progressAt"]


@pytest.mark.feature("INF-10")
def test_the_session_start_hook_injects_the_report_and_says_to_resume_first(
    monkeypatch: pytest.MonkeyPatch, checkpoint: Path
) -> None:
    """Running resume.py first was only advisory; Claude Code's SessionStart hook runs it for
    every new, resumed, cleared or compacted session and puts the report in its context."""
    monkeypatch.setattr(resume, "uncommitted", list)
    monkeypatch.setattr(resume, "wip_branches", list)
    interrupted = resume.hook_output(resume.begin("FRM-02", checkpoint, next_action="write the parser"))
    context = interrupted["hookSpecificOutput"]["additionalContext"]
    assert interrupted["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "did not finish" in context and "Before doing anything else" in context and "write the parser" in context
    clean = resume.hook_output(resume.end(checkpoint))["hookSpecificOutput"]["additionalContext"]
    assert "ended cleanly" in clean and "Before doing anything else" not in clean


@pytest.mark.feature("INF-10")
def test_hook_mode_prints_valid_json(
    monkeypatch: pytest.MonkeyPatch, checkpoint: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(resume, "CHECKPOINT", checkpoint)
    assert resume.main(["--hook"]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["hookEventName"] == "SessionStart"


@pytest.mark.feature("INF-10")
def test_the_project_settings_wire_the_hook_and_the_wait_for_the_limit_to_reset() -> None:
    """The two Claude Code settings this recovery depends on: without the SessionStart hook a
    new session doesn't see the report; without autoContinueAtUsageLimit a session stopped by a
    usage limit sits idle until someone types (the 2026-10-02 session sat 3.4 hours that way)."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [hook["command"] for entry in settings["hooks"]["SessionStart"] for hook in entry["hooks"]]
    assert any("tools/resume.py" in command and "--hook" in command for command in commands)
    assert settings.get("autoContinueAtUsageLimit") is True


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
    interrupted = resume.begin("FRM-01", checkpoint, next_action=CHECKPOINT["nextAction"])  # type: ignore[arg-type]
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
