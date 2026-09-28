"""COR-11: a local-only HTTP API server exposing the same Op layer the CLI
uses (SPEC.md section 4.2 rule 1 -- one code path per capability; section
8's architecture diagram; section 4.2 rule 5 -- local only).

The browser UI (P3 onward) talks to this server over JSON HTTP. Every route
except ``/health`` requires the ``X-Session-Token`` header to match a random
token generated when the app is created (:func:`create_app`), so a page in
another browser tab -- or another process on the machine -- cannot drive an
open document without that token. Binding the process to 127.0.0.1 only is
the caller's job (``server.run_server`` / ``pdfworkerz serve``); this module
never listens on a socket itself, which keeps it fully testable in-process
via ``fastapi.testclient.TestClient``.

Each open document is held server-side in an :class:`~engine.ops.journal.UndoRedoJournal`,
keyed by a random ``document_id`` -- the same undo/redo primitive COR-05
already built, now driving UI-04's history panel instead of a test.

Route handlers are module-level functions on a shared :class:`~fastapi.APIRouter`
(read the token and the open-document registry off ``request.app.state``
rather than closing over a particular app instance) so multiple independent
apps -- one per test -- can include the same router. A closure-based
dependency defined *inside* ``create_app()`` would also work at first glance,
but silently breaks: with ``from __future__ import annotations`` (used
throughout this codebase), FastAPI resolves an ``Annotated[X, Depends(f)]``
parameter by ``eval``-ing the stringified annotation against the function's
``__globals__``, and a dependency function that only exists as a local
variable inside ``create_app()`` isn't in those globals -- confirmed by
reproducing it directly against this FastAPI version before writing the
module-level version below.
"""

from __future__ import annotations

import base64
import dataclasses
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from engine import __version__
from engine.document import Document
from engine.errors import (
    CertificateEncryptedError,
    DocumentNotFoundError,
    FontResourceNotFoundError,
    NotAPdfError,
    NothingToUndoError,
    OpValidationError,
    OverwriteRefusedError,
    PasswordRequiredError,
    PdfWorkerzError,
    RepairFailedError,
    WrongPasswordError,
)
from engine.ops.base import PageSpansOp, RenderPageOp, parse_op
from engine.ops.journal import UndoRedoJournal
from engine.ops.text import PreviewTextOp

_STATUS_BY_ERROR: dict[type[PdfWorkerzError], int] = {
    DocumentNotFoundError: 404,
    NotAPdfError: 400,
    PasswordRequiredError: 401,
    WrongPasswordError: 401,
    CertificateEncryptedError: 422,
    RepairFailedError: 422,
    OverwriteRefusedError: 409,
    NothingToUndoError: 409,
    OpValidationError: 400,
    FontResourceNotFoundError: 422,
}


def _status_for(exc: PdfWorkerzError) -> int:
    for error_type, status in _STATUS_BY_ERROR.items():
        if isinstance(exc, error_type):
            return status
    return 400  # pragma: no cover -- every PdfWorkerzError subclass is mapped above


class OpenDocumentRequest(BaseModel):
    path: str
    password: str | None = None


class OpenDocumentResponse(BaseModel):
    document_id: str
    page_count: int
    is_encrypted: bool
    is_repaired: bool


class SaveRequest(BaseModel):
    path: str | None = None
    overwrite: bool = False
    mode: str = "auto"


class SaveResponse(BaseModel):
    path: str
    mode: str
    bytes_written: int


class HistoryResponse(BaseModel):
    ops: list[dict[str, Any]]
    can_undo: bool
    can_redo: bool


class UndoRedoResponse(BaseModel):
    op: dict[str, Any]


def _jsonable(result: Any) -> Any:
    """Op.apply() results are plain dataclasses, pydantic models, bytes, or
    lists of those -- never anything FastAPI can't already encode, except
    bytes (RenderPageOp's PNG), which is base64-encoded so the generic Op
    endpoint stays usable for *every* registered Op, including that one.
    Prefer GET .../render for actually fetching a page image; it returns
    the PNG directly instead of paying base64's ~33% size overhead."""
    if isinstance(result, bytes):
        return {"base64": base64.b64encode(result).decode("ascii")}
    if isinstance(result, list):
        return [_jsonable(item) for item in result]
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return dataclasses.asdict(result)
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    return result


def verify_token(request: Request, x_session_token: Annotated[str | None, Header()] = None) -> None:
    if x_session_token != request.app.state.session_token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Session-Token header")


def _get_journal(request: Request, document_id: str) -> UndoRedoJournal:
    journal = request.app.state.documents.get(document_id)
    if journal is None:
        raise HTTPException(status_code=404, detail=f"no open document with id {document_id!r}")
    journal_typed: UndoRedoJournal = journal
    return journal_typed


JournalDep = Annotated[UndoRedoJournal, Depends(_get_journal)]

router = APIRouter(dependencies=[Depends(verify_token)])


@router.post("/documents", response_model=OpenDocumentResponse)
def open_document(request: Request, body: OpenDocumentRequest) -> OpenDocumentResponse:
    document = Document.open(body.path, password=body.password)
    document_id = uuid.uuid4().hex
    request.app.state.documents[document_id] = UndoRedoJournal(document)
    return OpenDocumentResponse(
        document_id=document_id,
        page_count=document.page_count,
        is_encrypted=document.is_encrypted,
        is_repaired=document.is_repaired,
    )


@router.get("/documents/{document_id}")
def inspect_document_route(document_id: str, journal: JournalDep) -> Any:
    return _jsonable(journal.document.inspect())


@router.delete("/documents/{document_id}", status_code=204)
def close_document(request: Request, document_id: str, journal: JournalDep) -> Response:
    journal.document.close()
    del request.app.state.documents[document_id]
    return Response(status_code=204)


@router.post("/documents/{document_id}/ops")
def apply_op(document_id: str, body: dict[str, Any], journal: JournalDep) -> Any:
    op = parse_op(body)
    result = journal.record(op)
    return _jsonable(result)


@router.get("/documents/{document_id}/pages/{page_index}/render")
def render_page(document_id: str, page_index: int, journal: JournalDep, dpi: int = 150) -> Response:
    png_bytes = RenderPageOp(page_index=page_index, dpi=dpi).apply(journal.document)
    return Response(content=png_bytes, media_type="image/png")


@router.get("/documents/{document_id}/pages/{page_index}/render/original")
def render_original_page(document_id: str, page_index: int, journal: JournalDep, dpi: int = 150) -> Response:
    """UI-05's before/after split view: the page as it looked when first
    opened, regardless of how many edits (or undos) have happened since.
    Renders from a throwaway Document over journal.original_bytes -- never
    the live one -- so this never shows up in, or is affected by, undo/redo."""
    scratch = Document.from_bytes(journal.original_bytes)
    try:
        png_bytes = RenderPageOp(page_index=page_index, dpi=dpi).apply(scratch)
    finally:
        scratch.close()
    return Response(content=png_bytes, media_type="image/png")


@router.get("/documents/{document_id}/pages/{page_index}/spans")
def page_spans(document_id: str, page_index: int, journal: JournalDep) -> Any:
    """UI-02's click-to-edit overlay and UI-03's inspector panel both read
    this. Read-only (PageSpansOp), so -- like render_page above -- it's
    applied directly rather than through the undo/redo journal."""
    return _jsonable(PageSpansOp(page_index=page_index).apply(journal.document))


@router.get("/documents/{document_id}/pages/{page_index}/preview")
def preview_text(document_id: str, page_index: int, span_index: int, needed_text: str, journal: JournalDep) -> Any:
    """UI-02's live "Match" preview as the user types, before anything is
    committed. Read-only (PreviewTextOp), applied directly like page_spans."""
    op = PreviewTextOp(page_index=page_index, span_index=span_index, needed_text=needed_text)
    return _jsonable(op.apply(journal.document))


@router.get("/documents/{document_id}/file")
def document_file(document_id: str, journal: JournalDep) -> Response:
    """The document's current state (post-edit, pre-save) as raw PDF bytes --
    UI-01's page canvas loads this into pdf.js for client-side rendering,
    rather than round-tripping every page through the PNG render route."""
    return Response(content=journal.document.to_bytes(), media_type="application/pdf")


@router.post("/documents/{document_id}/undo", response_model=UndoRedoResponse)
def undo(document_id: str, journal: JournalDep) -> UndoRedoResponse:
    undone = journal.undo()
    return UndoRedoResponse(op=undone.model_dump())


@router.post("/documents/{document_id}/redo", response_model=UndoRedoResponse)
def redo(document_id: str, journal: JournalDep) -> UndoRedoResponse:
    redone = journal.redo()
    return UndoRedoResponse(op=redone.model_dump())


@router.get("/documents/{document_id}/history", response_model=HistoryResponse)
def history(document_id: str, journal: JournalDep) -> HistoryResponse:
    return HistoryResponse(
        ops=[op.model_dump() for op in journal.history],
        can_undo=journal.can_undo,
        can_redo=journal.can_redo,
    )


@router.post("/documents/{document_id}/save", response_model=SaveResponse)
def save(document_id: str, body: SaveRequest, journal: JournalDep) -> SaveResponse:
    result = journal.document.save(body.path, overwrite=body.overwrite, mode=body.mode)
    return SaveResponse(path=str(result.path), mode=result.mode, bytes_written=result.bytes_written)


_LOOPBACK_ORIGIN = r"^https?://(127\.0\.0\.1|\[::1\]|localhost)(:\d+)?$"


def create_app(*, token: str | None = None) -> FastAPI:
    """Build the server. ``token`` is normally left as None (a fresh random
    token is generated); tests pass a known value so they can authenticate."""
    app = FastAPI(title="PDFWorkerz", version=__version__)
    app.state.session_token = token or secrets.token_urlsafe(32)
    app.state.documents = {}
    app.include_router(router)
    # The browser UI (web/) and this API are two different origins/ports even
    # when both run on the same machine (SPEC.md section 4.2 rule 5: "local
    # only" describes the *listening address*, not that UI and API must share
    # one origin) -- without this, the browser's own CORS check blocks every
    # request before it reaches verify_token at all. Scoped to loopback
    # origins only, never a wildcard: authentication here is a header
    # (X-Session-Token), which -- unlike a cookie -- a browser never attaches
    # automatically, so a page on some other origin still can't act as this
    # user without already knowing the random token; restricting the origin
    # regex is a defense-in-depth boundary on top of that, not the primary one.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_LOOPBACK_ORIGIN,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(PdfWorkerzError)
    def handle_engine_error(_request: Request, exc: PdfWorkerzError) -> JSONResponse:
        return JSONResponse(status_code=_status_for(exc), content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def run_server(app: FastAPI, *, port: int = 8000) -> None:
    """Serve ``app`` on 127.0.0.1 only (SPEC.md section 4.2 rule 5: "local
    only"). Never pass a different host here -- binding wider is exactly
    what the random session token in :func:`create_app` exists to make
    pointless to attempt from outside this machine, but there's no reason
    to widen the actual listening address too."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port)
