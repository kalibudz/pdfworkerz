"""EDT-16: GET /documents/{id}/pages/{p}/text_units (SPEC.md 8.2 item 6's contract)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.corpus.build_corpus import Corpus

TOKEN = "test-session-token"
AUTH = {"X-Session-Token": TOKEN}
UNIT_KEYS = {
    "granularity",
    "index",
    "text",
    "bbox",
    "origin",
    "rotation_degrees",
    "span_indices",
    "segments",
    "line_index",
    "icon",
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(token=TOKEN))


@pytest.fixture
def document_id(client: TestClient, corpus: Corpus, work_dir: Path) -> str:
    dest = work_dir / "paragraph.pdf"
    shutil.copy(corpus.paragraph, dest)
    response = client.post("/documents", json={"path": str(dest)}, headers=AUTH)
    assert response.status_code == 200, response.text
    value: str = response.json()["document_id"]
    return value


@pytest.fixture
def icon_document_id(client: TestClient, corpus: Corpus, work_dir: Path) -> str:
    dest = work_dir / "icon_labels.pdf"
    shutil.copy(corpus.icon_labels, dest)
    response = client.post("/documents", json={"path": str(dest)}, headers=AUTH)
    assert response.status_code == 200, response.text
    value: str = response.json()["document_id"]
    return value


def _units(client: TestClient, document_id: str, **params: str) -> list[dict[str, object]]:
    response = client.get(f"/documents/{document_id}/pages/0/text_units", params=params, headers=AUTH)
    assert response.status_code == 200, response.text
    body: list[dict[str, object]] = response.json()
    return body


@pytest.mark.feature("EDT-16", criterion=3)
def test_the_default_granularity_is_line_and_matches_the_contract(client: TestClient, document_id: str) -> None:
    lines = _units(client, document_id)
    assert lines == _units(client, document_id, granularity="line")
    assert [u["text"] for u in lines] == [
        "This is line one of a paragraph.",
        "This is line two continuing on.",
        "And this is line three, the last.",
        "A separate paragraph starts here.",
    ]
    for position, unit in enumerate(lines):
        assert set(unit) == UNIT_KEYS
        assert unit["granularity"] == "line" and unit["index"] == position and unit["line_index"] is None
        assert len(unit["bbox"]) == 4 and len(unit["origin"]) == 2  # type: ignore[arg-type]
        assert unit["rotation_degrees"] == 0.0
        assert unit["segments"] == [{"span_index": position, "start": 0, "end": len(unit["text"])}]  # type: ignore[arg-type]
        assert unit["span_indices"] == [position]


@pytest.mark.feature("EDT-16", criterion=3)
def test_words_carry_their_line_index(client: TestClient, document_id: str) -> None:
    words = _units(client, document_id, granularity="word")
    assert [w["text"] for w in words[:7]] == ["This", "is", "line", "one", "of", "a", "paragraph."]
    assert [w["index"] for w in words] == list(range(len(words)))
    assert words[0]["line_index"] == 0 and words[-1]["line_index"] == 3
    assert words[6]["segments"] == [{"span_index": 0, "start": 22, "end": 32}]


@pytest.mark.feature("EDT-16", criterion=3)
def test_block_granularity_equals_the_blocks_route(client: TestClient, document_id: str) -> None:
    blocks = client.get(f"/documents/{document_id}/pages/0/blocks", headers=AUTH).json()
    units = _units(client, document_id, granularity="block")
    assert [u["span_indices"] for u in units] == [b["span_indices"] for b in blocks]
    assert [u["index"] for u in units] == [b["span_indices"][0] for b in blocks]
    assert [u["bbox"] for u in units] == [b["bbox"] for b in blocks]
    assert units[0]["text"] == (
        "This is line one of a paragraph. This is line two continuing on. And this is line three, the last."
    )


@pytest.mark.feature("FNT-20")
def test_word_units_report_icon_true_for_an_icon_and_false_for_ordinary_words(
    client: TestClient, icon_document_id: str
) -> None:
    words = _units(client, icon_document_id, granularity="word")
    by_text = {w["text"]: w["icon"] for w in words}
    assert by_text["✅"] is True
    assert by_text["OBJECTIVES"] is False
    assert by_text["©2026"] is False


@pytest.mark.feature("EDT-16", criterion=3)
def test_bad_requests_are_refused(client: TestClient, document_id: str) -> None:
    base = f"/documents/{document_id}/pages"
    assert client.get(f"{base}/0/text_units", params={"granularity": "letter"}, headers=AUTH).status_code == 422
    assert client.get(f"{base}/9/text_units", headers=AUTH).status_code == 400
    assert client.get(f"{base}/0/text_units").status_code == 401
    assert client.get("/documents/no-such-document/pages/0/text_units", headers=AUTH).status_code == 404
