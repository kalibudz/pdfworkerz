from __future__ import annotations

import pytest

import update_tracker


def _feat(fid: str = "EDT-01", status: str = "planned", criteria: list[str] | None = None) -> dict:
    feat = {"id": fid, "status": status}
    if criteria is not None:
        feat["criteria"] = criteria
    return feat


@pytest.mark.feature("INF-05")
def test_untested_feature_has_no_evidence() -> None:
    assert update_tracker.evidence_status(_feat(), {}) is None


@pytest.mark.feature("INF-05")
def test_passing_tests_prove_done() -> None:
    results = {"EDT-01": {"1": {"passed": 2, "failed": 0}}}
    assert update_tracker.evidence_status(_feat(), results) == "done"


@pytest.mark.feature("INF-05")
def test_any_failure_means_failing() -> None:
    results = {"EDT-01": {"1": {"passed": 5, "failed": 1}}}
    assert update_tracker.evidence_status(_feat(), results) == "failing"


@pytest.mark.feature("INF-05")
def test_uncovered_criteria_mean_in_progress() -> None:
    feat = _feat(criteria=["replaces text", "matches font", "passes pixel diff"])
    results = {"EDT-01": {"1": {"passed": 1, "failed": 0}, "2": {"passed": 1, "failed": 0}}}
    assert update_tracker.evidence_status(feat, results) == "in-progress"
    results["EDT-01"]["3"] = {"passed": 1, "failed": 0}
    assert update_tracker.evidence_status(feat, results) == "done"


@pytest.mark.feature("INF-05")
def test_sync_withdraws_claims_without_evidence() -> None:
    data = {"features": [_feat("A-01", "done"), _feat("B-01", "blocked"), _feat("C-01", "planned")]}
    results = {"C-01": {"1": {"passed": 1, "failed": 0}}}
    changes = update_tracker.sync(data, results)
    statuses = {f["id"]: f["status"] for f in data["features"]}
    assert statuses == {"A-01": "planned", "B-01": "blocked", "C-01": "done"}
    assert changes == ["A-01: done -> planned", "C-01: planned -> done"]


@pytest.mark.feature("INF-05")
def test_check_rejects_unsupported_done_claims() -> None:
    data = {"features": [_feat("A-01", "done"), _feat("B-01", "planned")]}
    results = {"B-01": {"1": {"passed": 1, "failed": 0}}}
    errors, notices = update_tracker.check(data, results)
    assert errors == ["A-01 is marked done but evidence says untested"]
    assert notices == ["B-01 has passing evidence; run --write to mark it done"]


@pytest.mark.feature("INF-05")
def test_load_results_missing_file_is_empty(tmp_path) -> None:
    assert update_tracker.load_results(tmp_path / "nope.json") == {}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the tools at a throwaway features.json and results file."""
    import features

    data = features.load()
    data["features"] = [dict(f, status="planned") for f in data["features"][:3]]
    data["features"][0]["status"] = "done"  # unsupported claim
    feats, results = tmp_path / "features.json", tmp_path / "results.json"
    features.save(data, feats)
    monkeypatch.setattr(features, "FEATURES_PATH", feats)
    monkeypatch.setattr(features, "RESULTS_PATH", results)
    return data, feats, results


@pytest.mark.feature("INF-05")
def test_cli_check_fails_on_unsupported_claim(sandbox, capsys) -> None:
    assert update_tracker.main(["--check"]) == 1
    assert "is marked done but evidence says untested" in capsys.readouterr().out


@pytest.mark.feature("INF-05")
def test_cli_write_then_check_passes(sandbox) -> None:
    import json

    import features

    data, feats, results = sandbox
    second = data["features"][1]["id"]
    results.write_text(json.dumps({second: {"1": {"passed": 1, "failed": 0}}}), encoding="utf-8")
    assert update_tracker.main(["--write"]) == 0
    statuses = [f["status"] for f in features.load(feats)["features"]]
    assert statuses == ["planned", "done", "planned"]
    assert update_tracker.main(["--check"]) == 0


@pytest.mark.feature("INF-05")
def test_cli_rejects_invalid_features_file(sandbox, capsys) -> None:
    import features

    data, feats, _ = sandbox
    data["features"][0]["phase"] = "P99"
    features.save(data, feats)
    assert update_tracker.main(["--check"]) == 1
    assert "features.json is invalid" in capsys.readouterr().out
