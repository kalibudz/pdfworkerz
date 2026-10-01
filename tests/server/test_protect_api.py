"""SEC-04/05/06 over the HTTP API: SetPasswordOp/RemovePasswordOp/SetPermissionsOp reached
through the existing generic, journaled ops endpoint (POST /documents/{id}/ops) -- no bespoke
protect/unlock routes were added. See engine.ops.protect and this test module's own docstring
notes below for why the generic endpoint already covers this correctly."""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.corpus.build_corpus import Corpus

TOKEN = "test-session-token"
AUTH = {"X-Session-Token": TOKEN}
USER_PW = "api-user-password"
OWNER_PW = "api-owner-password"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(token=TOKEN))


@pytest.fixture
def simple_path(corpus: Corpus, work_dir: Path) -> Path:
    dest = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, dest)
    return dest


@pytest.fixture
def encrypted_path(corpus: Corpus, work_dir: Path) -> Path:
    dest = work_dir / "encrypted.pdf"
    shutil.copy(corpus.encrypted_aes_256, dest)
    return dest


def _open(client: TestClient, path: Path, **kwargs: object) -> str:
    response = client.post("/documents", json={"path": str(path), **kwargs}, headers=AUTH)
    assert response.status_code == 200, response.text
    document_id: str = response.json()["document_id"]
    return document_id


@pytest.mark.feature("SEC-04", criterion=1)
def test_set_password_op_through_the_generic_ops_endpoint(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "set_password", "user_password": USER_PW, "path": str(simple_path), "overwrite": True},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == str(simple_path)
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(simple_path)


@pytest.mark.feature("SEC-04", criterion=1)
def test_set_password_without_either_password_is_a_400(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(f"/documents/{document_id}/ops", json={"op": "set_password"}, headers=AUTH)
    assert response.status_code == 400


@pytest.mark.feature("SEC-04", criterion=4)
def test_set_password_default_save_as_does_not_touch_the_open_file(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    original_bytes = simple_path.read_bytes()
    response = client.post(
        f"/documents/{document_id}/ops", json={"op": "set_password", "user_password": USER_PW}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    assert simple_path.read_bytes() == original_bytes


@pytest.mark.feature("SEC-05", criterion=1)
def test_remove_password_op_through_the_generic_ops_endpoint(
    client: TestClient, encrypted_path: Path, corpus: Corpus
) -> None:
    document_id = _open(client, encrypted_path, password=corpus.user_password)
    response = client.post(
        f"/documents/{document_id}/ops",
        json={"op": "remove_password", "path": str(encrypted_path), "overwrite": True},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    with pikepdf.open(encrypted_path):
        pass  # no password needed any more


@pytest.mark.feature("SEC-05", criterion=2)
def test_opening_with_a_wrong_password_never_reaches_remove_password(client: TestClient, encrypted_path: Path) -> None:
    """A wrong password fails at /documents (open), long before any ops call -- confirming
    there is no server-side path that lets a bad password reach SEC-05's removal logic."""
    response = client.post("/documents", json={"path": str(encrypted_path), "password": "wrong"}, headers=AUTH)
    assert response.status_code == 401


@pytest.mark.feature("SEC-06", criterion=3)
def test_set_permissions_without_owner_password_is_a_400(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(
        f"/documents/{document_id}/ops", json={"op": "set_permissions", "owner_password": ""}, headers=AUTH
    )
    assert response.status_code == 400


@pytest.mark.feature("SEC-06", criterion=1)
def test_set_permissions_denies_one_flag_independently(client: TestClient, simple_path: Path) -> None:
    document_id = _open(client, simple_path)
    response = client.post(
        f"/documents/{document_id}/ops",
        json={
            "op": "set_permissions",
            "owner_password": OWNER_PW,
            "allow_copy": False,
            "path": str(simple_path),
            "overwrite": True,
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    with pikepdf.open(simple_path) as pdf:
        assert pdf.allow.extract is False
        assert pdf.allow.print_lowres is True
