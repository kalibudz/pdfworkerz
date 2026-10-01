"""P6: the new FRM-03/07/08 server routes (server/app.py). A separate file from
tests/server/test_app.py to avoid two agents editing the same shared test file while
this and SIG-0x land in the same worktree concurrently."""

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from server.app import create_app

TOKEN = "test-session-token"
AUTH = {"X-Session-Token": TOKEN}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(token=TOKEN))


@pytest.fixture
def form_path(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "form.pdf"
    doc.save(path)
    doc.close()
    return path


def _open(client: TestClient, path: Path) -> str:
    response = client.post("/documents", json={"path": str(path)}, headers=AUTH)
    assert response.status_code == 200, response.text
    document_id: str = response.json()["document_id"]
    return document_id


@pytest.mark.feature("FRM-03")
def test_create_edit_delete_field_through_the_generic_ops_endpoint(client: TestClient, form_path: Path) -> None:
    document_id = _open(client, form_path)
    create = client.post(
        f"/documents/{document_id}/ops",
        json={
            "op": "create_field",
            "page_index": 0,
            "field_type": "text",
            "name": "full_name",
            "rect": [50, 50, 250, 70],
        },
        headers=AUTH,
    )
    assert create.status_code == 200, create.text
    assert create.json()["field_type"] == "text"

    edit = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "edit_field", "page_index": 0, "name": "full_name", "size": 14, "multiline": True},
        headers=AUTH,
    )
    assert edit.status_code == 200, edit.text

    fields = client.get(f"/documents/{document_id}/pages/0/fields", headers=AUTH)
    assert fields.status_code == 200
    assert [f["name"] for f in fields.json()] == ["full_name"]

    delete = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "delete_field", "page_index": 0, "name": "full_name"},
        headers=AUTH,
    )
    assert delete.status_code == 200, delete.text
    fields_after = client.get(f"/documents/{document_id}/pages/0/fields", headers=AUTH)
    assert fields_after.json() == []


@pytest.mark.feature("FRM-07")
def test_export_form_data_route_returns_the_requested_format(client: TestClient, form_path: Path) -> None:
    document_id = _open(client, form_path)
    client.post(
        f"/documents/{document_id}/ops",
        json={
            "op": "create_field",
            "page_index": 0,
            "field_type": "text",
            "name": "full_name",
            "rect": [50, 50, 250, 70],
        },
        headers=AUTH,
    )
    client.post(
        f"/documents/{document_id}/ops",
        json={"op": "fill_fields", "page_index": 0, "values": {"full_name": "Hello"}},
        headers=AUTH,
    )
    response = client.get(f"/documents/{document_id}/pages/0/form_data?format=json", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"full_name": "Hello"}


@pytest.mark.feature("FRM-07", criterion=2)
def test_import_form_data_op_fills_the_document(client: TestClient, form_path: Path) -> None:
    document_id = _open(client, form_path)
    client.post(
        f"/documents/{document_id}/ops",
        json={
            "op": "create_field",
            "page_index": 0,
            "field_type": "text",
            "name": "full_name",
            "rect": [50, 50, 250, 70],
        },
        headers=AUTH,
    )
    data_base64 = base64.b64encode(b'{"full_name": "Imported"}').decode("ascii")
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "import_form_data", "page_index": 0, "format": "json", "data_base64": data_base64},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json()["filled"] == ["full_name"]
    fields = client.get(f"/documents/{document_id}/pages/0/fields", headers=AUTH).json()
    assert fields[0]["value"] == "Imported"


@pytest.mark.feature("FRM-08")
def test_xfa_route_reports_no_xfa_on_a_plain_document(client: TestClient, form_path: Path) -> None:
    document_id = _open(client, form_path)
    response = client.get(f"/documents/{document_id}/xfa", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["has_xfa"] is False
    assert body["warning"] is None


@pytest.fixture
def flat_form_path(tmp_path: Path) -> Path:
    """A flat (non-interactive) page with one visual line+label cue."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 95), "Name:", fontsize=10)
    page.draw_line((100, 100), (300, 100), width=1)
    path = tmp_path / "flat_form.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.mark.feature("FRM-04")
def test_field_proposals_route_detects_a_line_and_apply_creates_it(client: TestClient, flat_form_path: Path) -> None:
    document_id = _open(client, flat_form_path)
    response = client.get(f"/documents/{document_id}/pages/0/field_proposals", headers=AUTH)
    assert response.status_code == 200, response.text
    proposals = response.json()
    assert len(proposals) == 1
    assert proposals[0]["field_type"] == "text"
    assert proposals[0]["label_text"] == "Name:"

    before_fields = client.get(f"/documents/{document_id}/pages/0/fields", headers=AUTH).json()
    assert before_fields == []  # detection alone creates nothing

    apply_response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "create_detected_fields", "page_index": 0, "proposals": proposals},
        headers=AUTH,
    )
    assert apply_response.status_code == 200, apply_response.text
    after_fields = client.get(f"/documents/{document_id}/pages/0/fields", headers=AUTH).json()
    assert len(after_fields) == 1
    assert after_fields[0]["field_type"] == "text"
