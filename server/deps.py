"""What every route module shares: the session-token check and the open-document lookup.

Route modules (``server.app``, ``server.transfer``, ``server.convert``) each own a router and are
included by ``create_app``; they read the token and the open-document registry off
``request.app.state`` rather than closing over one app, so independent apps (one per test) can
include the same routers.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel

from engine.document import Document
from engine.ops.journal import UndoRedoJournal


class OpenDocumentResponse(BaseModel):
    document_id: str
    page_count: int
    is_encrypted: bool
    is_repaired: bool
    name: str = ""
    """What to call the document: the file's name, or the suggested name of a converted one."""
    notes: list[str] = []
    """Anything a conversion could not carry across."""


def verify_token(request: Request, x_session_token: Annotated[str | None, Header()] = None) -> None:
    if x_session_token != request.app.state.session_token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Session-Token header")


def get_journal(request: Request, document_id: str) -> UndoRedoJournal:
    journal = request.app.state.documents.get(document_id)
    if journal is None:
        raise HTTPException(status_code=404, detail=f"no open document with id {document_id!r}")
    journal_typed: UndoRedoJournal = journal
    return journal_typed


JournalDep = Annotated[UndoRedoJournal, Depends(get_journal)]


def register_document(
    request: Request, document: Document, *, name: str = "", notes: list[str] | None = None
) -> OpenDocumentResponse:
    """Hold an opened document in the registry (with its undo history) and describe it."""
    document_id = uuid.uuid4().hex
    request.app.state.documents[document_id] = UndoRedoJournal(document)
    return OpenDocumentResponse(
        document_id=document_id,
        page_count=document.page_count,
        is_encrypted=document.is_encrypted,
        is_repaired=document.is_repaired,
        name=name or (document.source_path.name if document.source_path else ""),
        notes=notes or [],
    )
