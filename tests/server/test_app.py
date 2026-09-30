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


@pytest.mark.feature("UI-05")
def test_render_original_page_matches_the_live_render_before_any_edit(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    live = client.get(f"/documents/{document_id}/pages/0/render", headers=AUTH)
    original = client.get(f"/documents/{document_id}/pages/0/render/original", headers=AUTH)
    assert original.status_code == 200
    assert original.headers["content-type"] == "image/png"
    assert original.content == live.content


@pytest.mark.feature("UI-05")
def test_render_original_page_stays_unchanged_after_an_edit(client: TestClient, simple_path: Path) -> None:
    """UI-05's whole point: the "before" render must keep showing the
    document as it was opened, even after an edit changes the live one --
    and even after that edit is undone again."""
    document_id = _open(client, simple_path)
    original = client.get(f"/documents/{document_id}/pages/0/render/original", headers=AUTH)

    client.post(
        f"/documents/{document_id}/ops",
        json={"op": "replace_text", "match": "PDFWorkerz", "replacement": "Something Else Entirely"},
        headers=AUTH,
    )
    live_after_edit = client.get(f"/documents/{document_id}/pages/0/render", headers=AUTH)
    assert live_after_edit.content != original.content

    original_after_edit = client.get(f"/documents/{document_id}/pages/0/render/original", headers=AUTH)
    assert original_after_edit.content == original.content

    client.post(f"/documents/{document_id}/undo", headers=AUTH)
    original_after_undo = client.get(f"/documents/{document_id}/pages/0/render/original", headers=AUTH)
    assert original_after_undo.content == original.content


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


@pytest.mark.feature("EDT-07")
def test_copy_style_op_via_the_generic_ops_endpoint_is_undoable(
    client: TestClient, corpus: Corpus, work_dir: Path
) -> None:
    path = work_dir / "bold_italic.pdf"
    shutil.copy(corpus.bold_italic_standard, path)
    document_id = _open(client, path)

    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "copy_style", "page_index": 0, "span_index": 1, "target_page_index": 0, "target_span_index": 0},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "exact"

    history = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert history["ops"][0]["op"] == "copy_style"
    assert client.post(f"/documents/{document_id}/undo", headers=AUTH).status_code == 200


@pytest.mark.feature("EDT-10")
def test_links_route_reflects_journaled_link_ops_and_undo(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    links_url = f"/documents/{document_id}/pages/0/links"
    assert client.get(links_url, headers=AUTH).json() == []

    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "add_link", "page_index": 0, "rect": [72, 72, 200, 90], "uri": "https://example.com"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert [link["uri"] for link in client.get(links_url, headers=AUTH).json()] == ["https://example.com"]

    client.post(f"/documents/{document_id}/undo", headers=AUTH)
    assert client.get(links_url, headers=AUTH).json() == []


@pytest.mark.feature("ANN-06")
def test_annotations_route_reflects_annotation_ops_and_undo(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    url = f"/documents/{document_id}/pages/0/annotations"
    assert client.get(url, headers=AUTH).json() == []
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "add_note", "page_index": 0, "point": [100, 100], "text": "Check this", "author": "Kim"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    (note,) = client.get(url, headers=AUTH).json()
    assert (note["kind"], note["contents"], note["author"]) == ("Text", "Check this", "Kim")
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "delete_annotation", "page_index": 0, "xref": note["xref"] + 999},
        headers=AUTH,
    )
    assert response.status_code == 400 and "no annotation" in response.json()["detail"]
    client.post(f"/documents/{document_id}/undo", headers=AUTH)
    assert client.get(url, headers=AUTH).json() == []


@pytest.mark.feature("EDT-10")
def test_an_unsafe_link_is_a_400_validation_error(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "add_link", "page_index": 0, "rect": [72, 72, 200, 90], "uri": "javascript:alert(1)"},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "scheme" in response.json()["detail"]


@pytest.mark.feature("EDT-05")
def test_move_text_block_op_via_the_generic_ops_endpoint(client: TestClient, corpus: Corpus, work_dir: Path) -> None:
    path = work_dir / "paragraph.pdf"
    shutil.copy(corpus.paragraph, path)
    document_id = _open(client, path)
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "move_text_block", "page_index": 0, "span_index": 0, "dy": 200},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3  # one result per line of the paragraph
    spans = client.get(f"/documents/{document_id}/pages/0/spans", headers=AUTH).json()
    moved = next(s for s in spans if s["style"]["text"] == "This is line one of a paragraph.")
    assert moved["style"]["bbox"][1] > 250


@pytest.mark.feature("EDT-08")
def test_images_route_reflects_an_inserted_image_and_undo(client: TestClient, simple_path: Path) -> None:
    import base64
    import io

    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(png, format="PNG")
    document_id = _open(client, simple_path)
    images_url = f"/documents/{document_id}/pages/0/images"
    assert client.get(images_url, headers=AUTH).json() == []

    response = client.post(
        f"/documents/{document_id}/ops",
        json={
            "op": "insert_image",
            "page_index": 0,
            "rect": [72, 200, 172, 300],
            "image_base64": base64.b64encode(png.getvalue()).decode(),
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert len(client.get(images_url, headers=AUTH).json()) == 1

    client.post(f"/documents/{document_id}/undo", headers=AUTH)
    assert client.get(images_url, headers=AUTH).json() == []


@pytest.mark.feature("EDT-09")
def test_shapes_route_reflects_a_drawn_shape_and_undo(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    shapes_url = f"/documents/{document_id}/pages/0/shapes"
    assert client.get(shapes_url, headers=AUTH).json() == []
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "draw_shape", "page_index": 0, "kind": "rect", "points": [[72, 200], [272, 300]]},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert [s["kind"] for s in client.get(shapes_url, headers=AUTH).json()] == ["rect"]
    client.post(f"/documents/{document_id}/undo", headers=AUTH)
    assert client.get(shapes_url, headers=AUTH).json() == []


@pytest.mark.feature("EDT-11")
def test_spelling_route_and_correct_word_op(client: TestClient, work_dir: Path) -> None:
    import pymupdf

    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "Plese read teh notes.", fontsize=12, fontname="helv")
    path = work_dir / "typos.pdf"
    raw.save(path)
    document_id = _open(client, path)
    spelling_url = f"/documents/{document_id}/pages/0/spelling"

    found = client.get(spelling_url, headers=AUTH).json()
    assert [m["word"] for m in found] == ["Plese", "teh"]
    assert [m["word"] for m in client.get(f"{spelling_url}?ignore=teh", headers=AUTH).json()] == ["Plese"]

    teh = found[1]
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "correct_word", "page_index": 0, **{k: teh[k] for k in ("span_index", "start", "end", "word")}}
        | {"replacement": "the"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert [m["word"] for m in client.get(spelling_url, headers=AUTH).json()] == ["Plese"]


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


@pytest.mark.feature("COR-11")
def test_an_out_of_range_page_is_a_clear_400_not_a_server_error(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.get(f"/documents/{document_id}/pages/99/spans", headers=AUTH)
    assert response.status_code == 400
    assert "out of range" in response.json()["detail"]
    op = {"op": "replace_span_text", "page_index": 99, "span_index": 0, "new_text": "x"}
    assert client.post(f"/documents/{document_id}/ops", json=op, headers=AUTH).status_code == 400


@pytest.mark.feature("COR-11")
@pytest.mark.parametrize("dpi", [0, 5000])
def test_render_dpi_is_bounded(client: TestClient, simple_path: Path, dpi: int) -> None:
    document_id = _open(client, simple_path)
    assert client.get(f"/documents/{document_id}/pages/0/render?dpi={dpi}", headers=AUTH).status_code == 422


@pytest.mark.feature("COR-11")
def test_an_unknown_save_mode_is_refused(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(f"/documents/{document_id}/save", json={"mode": "fastest"}, headers=AUTH)
    assert response.status_code == 422


@pytest.mark.feature("COR-11")
def test_saving_a_signed_document_reports_that_its_signature_no_longer_applies(
    client: TestClient, corpus: Corpus, work_dir: Path
) -> None:
    signed = work_dir / "signed.pdf"
    shutil.copy(corpus.form_and_signature, signed)
    document_id = _open(client, signed)
    body = client.post(f"/documents/{document_id}/save", json={}, headers=AUTH).json()
    assert body["mode"] == "full" and "signature" in body["note"]


@pytest.mark.feature("COR-11")
def test_history_does_not_resend_inserted_image_data(client: TestClient, simple_path: Path) -> None:
    import base64
    import io

    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 255)).save(png, format="PNG")
    encoded = base64.b64encode(png.getvalue()).decode()
    document_id = _open(client, simple_path)
    op = {"op": "insert_image", "page_index": 0, "rect": [72, 200, 172, 300], "image_base64": encoded}
    assert client.post(f"/documents/{document_id}/ops", json=op, headers=AUTH).status_code == 200
    (entry,) = client.get(f"/documents/{document_id}/history", headers=AUTH).json()["ops"]
    assert entry["op"] == "insert_image"
    assert entry["image_base64"] == f"<{len(encoded) * 3 // 4} bytes>"


@pytest.mark.feature("SEC-03")
def test_download_keeps_the_original_encryption(client: TestClient, corpus: Corpus, work_dir: Path) -> None:
    import pymupdf

    source = work_dir / "aes256.pdf"
    shutil.copy(corpus.encrypted_aes_256, source)
    document_id = _open(client, source, password=corpus.user_password)
    op = {"op": "replace_text", "match": "quarterly", "replacement": "annual", "require_tier": "fallback"}
    assert client.post(f"/documents/{document_id}/ops", json=op, headers=AUTH).status_code == 200

    downloaded = client.get(f"/documents/{document_id}/download", headers=AUTH).content
    with pymupdf.open(stream=downloaded, filetype="pdf") as doc:
        assert doc.needs_pass
        assert doc.authenticate(corpus.user_password)
        assert doc[0].get_text().strip() == "Confidential: annual figures"
    # .../file stays decrypted on purpose: pdf.js renders it.
    rendered = client.get(f"/documents/{document_id}/file", headers=AUTH).content
    with pymupdf.open(stream=rendered, filetype="pdf") as doc:
        assert not doc.needs_pass


@pytest.mark.feature("EDT-06")
def test_fonts_route_lists_families_and_restyle_span_applies_one(client: TestClient, simple_path: Path) -> None:
    families = client.get("/fonts", headers=AUTH).json()["families"]
    assert families[:3] == ["Helvetica", "Times", "Courier"]
    document_id = _open(client, simple_path)
    op = {"op": "restyle_span", "page_index": 0, "span_index": 0, "font": "Times", "bold": True}
    assert client.post(f"/documents/{document_id}/ops", json=op, headers=AUTH).status_code == 200
    (span,) = client.get(f"/documents/{document_id}/pages/0/spans", headers=AUTH).json()
    assert span["style"]["font"] == "Times-Bold"


@pytest.mark.feature("FNT-06")
def test_fonts_research_route_returns_the_list(client: TestClient) -> None:
    from engine.fonts import research

    research.flag("MysteryGrotesk", tier="fallback", note="standard-font fallback", document="x.pdf")
    (row,) = client.get("/fonts/research", headers=AUTH).json()
    assert row["font"] == "MysteryGrotesk" and row["times_seen"] == 1


@pytest.mark.feature("FNT-06")
def test_font_library_routes_add_harvest_and_list(client: TestClient, work_dir: Path) -> None:
    from engine.fonts import research
    from tests.engine.test_font_library import _POSTSCRIPT, _pdf_with_font, _test_font

    source = _pdf_with_font(work_dir / "full.pdf", _test_font())
    document_id = _open(client, source)
    research.flag(_POSTSCRIPT, tier="approximate", note="look-alike", document="full.pdf")
    research.flag("NotInThisOne", tier="fallback", note="fallback", document="other.pdf")

    rows = {row["font"]: row for row in client.get(f"/fonts/research?document_id={document_id}", headers=AUTH).json()}
    assert rows[_POSTSCRIPT]["harvestable_here"] is True
    assert rows["NotInThisOne"]["harvestable_here"] is False
    assert "no font named" in rows["NotInThisOne"]["harvest_problem"]

    added = client.post(f"/documents/{document_id}/fonts/harvest", json={"font": _POSTSCRIPT}, headers=AUTH)
    assert added.status_code == 200 and added.json()["postscript_name"] == _POSTSCRIPT
    assert [row["font"] for row in client.get("/fonts/research", headers=AUTH).json()] == ["NotInThisOne"]
    assert [font["postscript_name"] for font in client.get("/fonts/library", headers=AUTH).json()] == [_POSTSCRIPT]
    assert "PWTestSans" in client.get("/fonts", headers=AUTH).json()["families"]

    refused = client.post(f"/documents/{document_id}/fonts/harvest", json={"font": "NotHere"}, headers=AUTH)
    assert refused.status_code == 400 and "no font named" in refused.json()["detail"]

    font_file = work_dir / "mine.ttf"
    font_file.write_bytes(_test_font(postscript="PWMine-Regular"))
    by_path = client.post("/fonts/library", json={"path": f'"{font_file}"'}, headers=AUTH)  # pasted with quotes
    assert by_path.status_code == 200 and by_path.json()["postscript_name"] == "PWMine-Regular"
    assert client.delete("/fonts/library/PWMine-Regular", headers=AUTH).status_code == 204
    assert client.delete("/fonts/library/PWMine-Regular", headers=AUTH).status_code == 400


@pytest.mark.feature("EDT-06")
def test_edit_span_over_the_api_is_one_history_entry(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    body = {"op": "edit_span", "page_index": 0, "span_index": 0, "new_text": "Hello, Editor.", "bold": True}
    assert client.post(f"/documents/{document_id}/ops", json=body, headers=AUTH).status_code == 200
    history = client.get(f"/documents/{document_id}/history", headers=AUTH).json()
    assert [op["op"] for op in history["ops"]] == ["edit_span"]


# -- P4: commands and recipes over the API --


@pytest.mark.feature("CMD-05")
def test_command_preview_counts_without_changing_and_apply_is_one_undo(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    base = f"/documents/{document_id}/commands"
    preview = client.post(f"{base}/preview", json={"text": 'replace "PDFWorkerz" with "Editor"'}, headers=AUTH).json()
    assert preview["matches"] == 1 and preview["pages"] == [1]
    assert preview["description"].startswith("Replace")
    assert client.get(f"/documents/{document_id}/history", headers=AUTH).json()["ops"] == []

    applied = client.post(f"{base}/apply", json={"text": 'replace "PDFWorkerz" with "Editor"'}, headers=AUTH)
    assert applied.status_code == 200
    (span,) = client.get(f"/documents/{document_id}/pages/0/spans", headers=AUTH).json()
    assert span["style"]["text"] == "Hello, Editor."
    client.post(f"{base}/apply", json={"text": "undo"}, headers=AUTH)
    (span,) = client.get(f"/documents/{document_id}/pages/0/spans", headers=AUTH).json()
    assert span["style"]["text"] == "Hello, PDFWorkerz."


@pytest.mark.feature("CMD-06")
def test_a_bad_command_returns_suggestions_and_a_hint(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(
        f"/documents/{document_id}/commands/preview", json={"text": 'repalce "a" with "b"'}, headers=AUTH
    )
    assert response.status_code == 400
    body = response.json()
    assert body["suggestions"][0] == 'replace "a" with "b"' and body["hint"]


@pytest.mark.feature("CMD-04")
def test_completion_route(client: TestClient) -> None:
    body = client.get("/commands/complete", params={"text": 'replace "a" '}, headers=AUTH).json()
    assert body["suggestions"] == ["with"]


@pytest.mark.feature("CMD-07")
def test_recipe_export_and_replay_over_the_api(
    client: TestClient, simple_path: Path, corpus: Corpus, work_dir: Path
) -> None:
    document_id = _open(client, simple_path)
    client.post(
        f"/documents/{document_id}/commands/apply", json={"text": 'replace "PDFWorkerz" with "Editor"'}, headers=AUTH
    )
    recipe = client.get(f"/documents/{document_id}/recipe", headers=AUTH).text
    assert "replace_text" in recipe and "recipe: simple" in recipe

    other = work_dir / "other.pdf"
    shutil.copy(corpus.simple, other)
    second = _open(client, other)
    dry = client.post(f"/documents/{second}/recipe", json={"text": recipe, "dry_run": True}, headers=AUTH).json()
    assert dry == {"recipe": "simple", "steps": 1, "matches": 1, "pages": [1], "warnings": [], "applied": False}
    assert client.post(f"/documents/{second}/recipe", json={"text": recipe}, headers=AUTH).json()["applied"]
    (span,) = client.get(f"/documents/{second}/pages/0/spans", headers=AUTH).json()
    assert span["style"]["text"] == "Hello, Editor."
    assert len(client.get(f"/documents/{second}/history", headers=AUTH).json()["ops"]) == 1


@pytest.mark.feature("CMD-07")
def test_recipe_and_preview_errors_are_400s_and_dry_runs_chain(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    bad_regex = "ops:\n  - {op: replace_text, match: '(', mode: regex}"
    response = client.post(f"/documents/{document_id}/recipe", json={"text": bad_regex, "dry_run": True}, headers=AUTH)
    assert response.status_code == 400 and "step 1" in response.json()["detail"]
    preview = client.post(
        f"/documents/{document_id}/commands/preview",
        json={"text": 'insert "x" below "Hello"', "page_index": 99},
        headers=AUTH,
    )
    assert preview.status_code == 400
    chained = (
        "ops:\n  - {op: replace_text, match: PDFWorkerz, replacement: Editor}\n"
        "  - {op: restyle_text, match: Editor, bold: true}\n"
    )
    dry = client.post(f"/documents/{document_id}/recipe", json={"text": chained, "dry_run": True}, headers=AUTH)
    assert dry.json()["matches"] == 2 and dry.json()["warnings"] == []


@pytest.mark.feature("ORG-04")
def test_page_ops_over_the_api_and_undo(client: TestClient, corpus: Corpus, work_dir: Path) -> None:
    path = work_dir / "multi.pdf"
    shutil.copy(corpus.multi_page, path)
    document_id = _open(client, path)
    base = f"/documents/{document_id}"
    assert client.post(f"{base}/commands/apply", json={"text": "delete pages 1-2"}, headers=AUTH).status_code == 200
    spans = client.get(f"{base}/pages/0/spans", headers=AUTH).json()
    assert spans[0]["style"]["text"] == "Page 3 of 5"
    client.post(f"{base}/undo", headers=AUTH)
    assert client.get(f"{base}/pages/0/spans", headers=AUTH).json()[0]["style"]["text"] == "Page 1 of 5"
