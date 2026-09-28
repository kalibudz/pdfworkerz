"""COR-11: the local HTTP API server."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.corpus.build_corpus import Corpus

TOKEN = "test-session-token"
AUTH = {"X-Session-Token": TOKEN}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(token=TOKEN))


@pytest.fixture
def simple_path(corpus: Corpus, work_dir: Path) -> Path:
    dest = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, dest)
    return dest


def _open(client: TestClient, path: Path, **kwargs: object) -> str:
    response = client.post("/documents", json={"path": str(path), **kwargs}, headers=AUTH)
    assert response.status_code == 200, response.text
    document_id: str = response.json()["document_id"]
    return document_id


@pytest.mark.feature("COR-11")
def test_health_needs_no_auth(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.feature("COR-11")
def test_cors_allows_a_loopback_origin(client: TestClient) -> None:
    """The browser UI (web/) is always a different origin from this API, even
    on the same machine -- confirms the CORS policy actually grants it, not
    just that it's configured with the right-looking regex."""
    response = client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


@pytest.mark.feature("COR-11")
def test_cors_does_not_allow_a_non_loopback_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.feature("COR-11")
def test_create_app_generates_a_random_token_by_default() -> None:
    first = create_app()
    second = create_app()
    assert first.state.session_token != second.state.session_token
    assert len(first.state.session_token) > 20  # a real random token, not a short/predictable one


@pytest.mark.feature("COR-11")
def test_protected_route_without_token_is_rejected(client: TestClient, simple_path: Path) -> None:
    response = client.post("/documents", json={"path": str(simple_path)})
    assert response.status_code == 401


@pytest.mark.feature("COR-11")
def test_protected_route_with_wrong_token_is_rejected(client: TestClient, simple_path: Path) -> None:
    response = client.post(
        "/documents", json={"path": str(simple_path)}, headers={"X-Session-Token": "not-the-real-token"}
    )
    assert response.status_code == 401


@pytest.mark.feature("COR-11")
def test_open_document_returns_its_page_count(client: TestClient, simple_path: Path) -> None:
    response = client.post("/documents", json={"path": str(simple_path)}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["page_count"] == 1
    assert body["is_encrypted"] is False
    assert isinstance(body["document_id"], str) and body["document_id"]


@pytest.mark.feature("COR-11")
def test_open_document_missing_file_returns_404(client: TestClient, work_dir: Path) -> None:
    response = client.post("/documents", json={"path": str(work_dir / "nope.pdf")}, headers=AUTH)
    assert response.status_code == 404


@pytest.mark.feature("COR-11")
def test_open_encrypted_document_without_password_returns_401(client: TestClient, corpus: Corpus) -> None:
    response = client.post("/documents", json={"path": str(corpus.encrypted_aes_256)}, headers=AUTH)
    assert response.status_code == 401


@pytest.mark.feature("COR-11")
def test_open_encrypted_document_with_wrong_password_returns_401(client: TestClient, corpus: Corpus) -> None:
    response = client.post(
        "/documents", json={"path": str(corpus.encrypted_aes_256), "password": "definitely-wrong"}, headers=AUTH
    )
    assert response.status_code == 401


@pytest.mark.feature("COR-11")
def test_open_encrypted_document_with_correct_password_succeeds(client: TestClient, corpus: Corpus) -> None:
    response = client.post(
        "/documents",
        json={"path": str(corpus.encrypted_aes_256), "password": corpus.user_password},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["is_encrypted"] is True


@pytest.mark.feature("COR-11")
def test_inspect_route_returns_the_inspection_report(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.get(f"/documents/{document_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["page_count"] == 1


@pytest.mark.feature("COR-11")
def test_unknown_document_id_returns_404(client: TestClient) -> None:
    response = client.get("/documents/no-such-id", headers=AUTH)
    assert response.status_code == 404


@pytest.mark.feature("COR-11")
def test_close_document_then_using_it_returns_404(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    assert client.delete(f"/documents/{document_id}", headers=AUTH).status_code == 204
    assert client.get(f"/documents/{document_id}", headers=AUTH).status_code == 404


@pytest.mark.feature("COR-11")
def test_apply_op_runs_a_real_edit_and_returns_its_result(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "replace_text", "match": "PDFWorkerz", "replacement": "API Editor"},
        headers=AUTH,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["tier"] == "exact"
    assert results[0]["requires_approval"] is False


@pytest.mark.feature("COR-11")
def test_apply_op_with_an_unknown_op_name_returns_400(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(f"/documents/{document_id}/ops", json={"op": "not_a_real_op"}, headers=AUTH)
    assert response.status_code == 400


@pytest.mark.feature("COR-11")
def test_apply_op_with_a_missing_required_field_returns_400(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(f"/documents/{document_id}/ops", json={"op": "replace_text"}, headers=AUTH)
    assert response.status_code == 400


@pytest.mark.feature("COR-11")
def test_render_page_returns_png_bytes(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.get(f"/documents/{document_id}/pages/0/render", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.feature("COR-11")
def test_page_spans_route_returns_every_span(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.get(f"/documents/{document_id}/pages/0/spans", headers=AUTH)
    assert response.status_code == 200
    spans = response.json()
    assert len(spans) == 1
    assert spans[0]["style"]["text"] == "Hello, PDFWorkerz."
    assert spans[0]["style"]["span_index"] == 0


@pytest.mark.feature("COR-11")
def test_preview_route_reports_an_exact_match(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.get(
        f"/documents/{document_id}/pages/0/preview",
        params={"span_index": 0, "needed_text": "Editor"},
        headers=AUTH,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "exact"
    assert body["requires_approval"] is False


@pytest.mark.feature("COR-11")
def test_preview_route_with_an_out_of_range_span_index_returns_400(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.get(
        f"/documents/{document_id}/pages/0/preview",
        params={"span_index": 99, "needed_text": "x"},
        headers=AUTH,
    )
    assert response.status_code == 400


@pytest.mark.feature("COR-11")
def test_preview_route_never_appears_in_history(client: TestClient, simple_path: Path) -> None:
    """PreviewTextOp is read-only and must never be journaled -- confirms
    it's actually wired to a direct apply() call, not the generic (and
    journaled) POST .../ops endpoint."""
    document_id = _open(client, simple_path)
    client.get(
        f"/documents/{document_id}/pages/0/preview",
        params={"span_index": 0, "needed_text": "Editor"},
        headers=AUTH,
    )
    history = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert history["ops"] == []
    assert history["can_undo"] is False


@pytest.mark.feature("COR-11")
def test_replace_span_text_op_via_the_generic_ops_endpoint(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "replace_span_text", "page_index": 0, "span_index": 0, "new_text": "Hello, Editor."},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["tier"] == "exact"

    history = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert len(history["ops"]) == 1
    assert history["can_undo"] is True


@pytest.mark.feature("UI-04")
def test_replace_span_text_op_round_trips_through_history_and_undo(client: TestClient, simple_path: Path) -> None:
    """UI-04's history panel describes each entry from the Op's own fields
    (web/src/history.ts's describeOp) -- so those fields, not just an op
    count, need to actually be there. Also confirms undo/redo work for this
    Op specifically, not just for the generic replace_text already covered
    by test_undo_redo_round_trip_through_the_api."""
    document_id = _open(client, simple_path)
    client.post(
        f"/documents/{document_id}/ops",
        json={"op": "replace_span_text", "page_index": 0, "span_index": 0, "new_text": "Hello, Editor."},
        headers=AUTH,
    )

    history = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert history["ops"] == [
        {
            "op": "replace_span_text",
            "page_index": 0,
            "span_index": 0,
            "new_text": "Hello, Editor.",
            "require_tier": "approximate",
            "fit": False,
            "verify": True,
        }
    ]

    undo_response = client.post(f"/documents/{document_id}/undo", headers=AUTH)
    assert undo_response.status_code == 200
    assert undo_response.json()["op"]["new_text"] == "Hello, Editor."
    assert client.get(f"/documents/{document_id}/history", headers=AUTH).json()["can_redo"] is True

    redo_response = client.post(f"/documents/{document_id}/redo", headers=AUTH)
    assert redo_response.status_code == 200
    assert redo_response.json()["op"]["new_text"] == "Hello, Editor."


@pytest.mark.feature("COR-11")
def test_document_file_route_returns_the_current_pdf_bytes(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.get(f"/documents/{document_id}/file", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


@pytest.mark.feature("COR-11")
def test_document_file_route_reflects_edits_already_applied(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    before = client.get(f"/documents/{document_id}/file", headers=AUTH).content
    client.post(
        f"/documents/{document_id}/ops",
        json={"op": "replace_text", "match": "PDFWorkerz", "replacement": "Changed"},
        headers=AUTH,
    )
    after = client.get(f"/documents/{document_id}/file", headers=AUTH).content
    assert before != after


@pytest.mark.feature("COR-11")
def test_undo_redo_round_trip_through_the_api(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    client.post(
        f"/documents/{document_id}/ops",
        json={"op": "replace_text", "match": "PDFWorkerz", "replacement": "Changed"},
        headers=AUTH,
    )

    history = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert len(history["ops"]) == 1
    assert history["can_undo"] is True
    assert history["can_redo"] is False

    undo_response = client.post(f"/documents/{document_id}/undo", headers=AUTH)
    assert undo_response.status_code == 200
    assert undo_response.json()["op"]["replacement"] == "Changed"

    after_undo = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert after_undo["can_undo"] is False
    assert after_undo["can_redo"] is True

    redo_response = client.post(f"/documents/{document_id}/redo", headers=AUTH)
    assert redo_response.status_code == 200

    after_redo = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert after_redo["can_undo"] is True
    assert after_redo["can_redo"] is False


@pytest.mark.feature("COR-11")
def test_undo_with_nothing_to_undo_returns_409(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(f"/documents/{document_id}/undo", headers=AUTH)
    assert response.status_code == 409


@pytest.mark.feature("COR-11")
def test_save_writes_a_versioned_file_next_to_the_source(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(f"/documents/{document_id}/save", json={}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["bytes_written"] > 0
    saved_path = Path(body["path"])
    assert saved_path.exists()
    assert saved_path != simple_path  # the original is never overwritten by default (COR-08)


@pytest.mark.feature("COR-11")
def test_save_with_overwrite_writes_back_to_the_source(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(f"/documents/{document_id}/save", json={"overwrite": True}, headers=AUTH)
    assert response.status_code == 200
    assert Path(response.json()["path"]) == simple_path
