"""Evidence plugin: link tests to feature IDs and record the outcomes.

Mark a test with ``@pytest.mark.feature("EDT-01")`` (optionally ``criterion=2``).
Run ``pytest --feature-results`` to write build/feature_results.json, which
tools/update_tracker.py uses to decide which features are proven done. Only
full-suite runs should pass the flag, because a partial run would look like
missing evidence.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from tests.corpus.build_corpus import Corpus, build_corpus

RESULTS_PATH = Path(__file__).resolve().parent.parent / "build" / "feature_results.json"


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    """The shared golden PDF corpus (INF-06), built once per test session."""
    return build_corpus()


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own PDFWORKERZ_DATA_DIR, so nothing (e.g. the fonts-to-research
    list) is ever written to the real user data folder."""
    data = tmp_path / "pdfworkerz-data"
    monkeypatch.setenv("PDFWORKERZ_DATA_DIR", str(data))
    return data


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    """A throwaway directory for tests that write files."""
    return tmp_path


_links: dict[str, list[tuple[str, int]]] = {}
_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"passed": 0, "failed": 0}))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--feature-results",
        action="store_true",
        default=False,
        help="write build/feature_results.json for the tracker evidence gate",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "feature(id, criterion=1): the feature this test proves")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        links = []
        for mark in item.iter_markers(name="feature"):
            if len(mark.args) != 1:
                raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.feature takes exactly one feature id")
            links.append((str(mark.args[0]), int(mark.kwargs.get("criterion", 1))))
        if links:
            _links[item.nodeid] = links


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    links = _links.get(report.nodeid)
    if not links:
        return
    # Count the call phase, plus setup/teardown errors (a broken fixture is a failure too).
    if report.when == "call" or report.failed:
        outcome = "failed" if report.failed else "passed" if report.passed else None
        if outcome is None:  # skipped tests are not evidence either way
            return
        for feature_id, criterion in links:
            _counts[feature_id][str(criterion)][outcome] += 1


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not session.config.getoption("--feature-results"):
        return
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {fid: dict(crits) for fid, crits in sorted(_counts.items())}
    RESULTS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
