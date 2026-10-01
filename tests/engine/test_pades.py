"""SIG-02/03/04/05: PAdES digital signing, its validation report, RFC 3161
timestamps, and the signed-document save/warning workflow -- built on
engine.pades, clearly separate from SIG-01's purely visual stamp
(tests/engine/test_signatures.py)."""

from __future__ import annotations

import datetime as _dt
import http.server
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from asn1crypto import tsp
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID
from pyhanko.keys import pemder
from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper

from engine.certs import CertResult, generate_self_signed_cert
from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import parse_op
from engine.ops.design import PageNumbersOp
from engine.ops.journal import UndoRedoJournal
from engine.ops.sign import DocumentSignatureStatusOp, SignDocumentOp, ValidateSignaturesOp
from engine.pades import document_signature_status, sign_document, validate_signatures


@pytest.fixture
def doc_path(work_dir: Path) -> Path:
    raw = pymupdf.open()
    raw.new_page()
    path = work_dir / "pades.pdf"
    raw.save(path)
    raw.close()
    return path


@pytest.fixture
def doc(doc_path: Path) -> Iterator[Document]:
    document = Document.open(doc_path)
    yield document
    document.close()


@pytest.fixture
def cert() -> CertResult:
    return generate_self_signed_cert("Jane Doe")


def _expired_cert() -> CertResult:
    """A certificate engine.certs can't produce (it has no validity-window
    override by design -- see its module docstring): built directly with
    `cryptography`, matching the orchestrator's guidance for this one case."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Expired Signer")])
    now = _dt.datetime.now(_dt.UTC)
    cert_obj = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=100))
        .not_valid_after(now - _dt.timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
    cert_pem = cert_obj.public_bytes(Encoding.PEM).decode("ascii")
    return CertResult(
        common_name="Expired Signer",
        key_size=2048,
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_encrypted=False,
        pkcs12_base64="",
    )


class _MockTsa:
    """A tiny local RFC 3161 TSA: an `http.server` wrapping pyHanko's own
    `DummyTimeStamper` (its built-in in-process test utility) so SIG-04's
    positive path -- a real `tsa_url` HTTP round trip -- is tested without
    any real network access or a flaky public TSA. See engine.pades module
    docstring / this session's report for why: pyHanko ships no HTTP mock of
    its own, only this in-process one, so this file supplies the thinnest
    possible HTTP wrapper around it."""

    def __init__(self) -> None:
        self.cert = generate_self_signed_cert("Mock TSA")
        tsa_key = pemder.load_private_key_from_pemder_data(self.cert.key_pem.encode("ascii"), passphrase=None)
        tsa_cert = next(pemder.load_certs_from_pemder_data(self.cert.cert_pem.encode("ascii")))
        self._dummy = DummyTimeStamper(tsa_cert=tsa_cert, tsa_key=tsa_key)
        handler = self._make_handler()
        self._server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _make_handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        dummy = self._dummy

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # quiet: don't spam test output
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                req = tsp.TimeStampReq.load(body)
                resp = dummy.request_tsa_response(req)
                data = resp.dump()
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def shutdown(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)


@pytest.fixture
def mock_tsa() -> Iterator[_MockTsa]:
    server = _MockTsa()
    yield server
    server.shutdown()


@pytest.fixture
def unreachable_tsa_url() -> str:
    """A URL nothing listens on, for a fast, clean connection failure --
    not a real network call (127.0.0.1 only) and not a slow timeout either:
    confirmed this refuses the connection immediately rather than hanging."""
    return "http://127.0.0.1:1/"


# -- SIG-02: PAdES signing ---------------------------------------------------


@pytest.mark.feature("SIG-02", criterion=2)
def test_signing_refuses_a_pending_unsaved_edit_instead_of_silently_dropping_it(
    doc: Document, cert: CertResult
) -> None:
    """Found by independent review: the precondition this function's own docstring
    claimed ("no unsaved pending edits") was never actually enforced -- `file_backed`
    only tracks whether the document was ever reloaded from an undo/redo snapshot, not
    whether memory has since diverged from disk. An edit applied through the normal
    journaled Op path (exactly how the server's generic /ops endpoint works) used to
    sail straight past the check, sign_document then silently signed the *stale* bytes
    already on disk, reported success, and reload() threw the caller's pending edit away
    with no warning. Regression test for engine.document's new dirty-tracking."""
    journal = UndoRedoJournal(doc)
    journal.record(PageNumbersOp())  # an ordinary edit, applied the way the server does
    assert doc.dirty is True
    with pytest.raises(OpValidationError):
        sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    # And the edit must still be sitting in memory, unsigned and undiscarded --
    # not silently dropped by a sign_document that proceeded anyway.
    assert doc.dirty is True
    assert validate_signatures(doc) == []


@pytest.mark.feature("SIG-02", criterion=1)
def test_sign_produces_a_pades_signature(doc: Document, cert: CertResult) -> None:
    result = sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem, reason="testing", location="here")
    assert result.pades_level == "B-B"
    assert result.field_name
    reports = validate_signatures(doc)
    assert len(reports) == 1
    assert reports[0].intact is True
    assert reports[0].signature_valid is True


@pytest.mark.feature("SIG-02", criterion=2)
def test_signing_is_incremental_and_preserves_bytes_verbatim(doc: Document, cert: CertResult) -> None:
    original_bytes = doc.source_path.read_bytes()
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    signed_bytes = doc.source_path.read_bytes()
    assert len(signed_bytes) > len(original_bytes)
    assert signed_bytes[: len(original_bytes)] == original_bytes


@pytest.mark.feature("SIG-02", criterion=2)
def test_second_signature_preserves_the_first_signed_bytes_verbatim(doc: Document, cert: CertResult) -> None:
    """A countersignature is itself just another incremental update on top
    of the first -- the first signature's own bytes must still be untouched."""
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem, field_name="Signature1")
    once_signed_bytes = doc.source_path.read_bytes()
    cert2 = generate_self_signed_cert("Second Signer")
    sign_document(doc, cert_pem=cert2.cert_pem, key_pem=cert2.key_pem, field_name="Signature2")
    twice_signed_bytes = doc.source_path.read_bytes()
    assert twice_signed_bytes[: len(once_signed_bytes)] == once_signed_bytes


@pytest.mark.feature("SIG-02", criterion=3)
def test_sign_self_validates_before_returning(doc: Document, cert: CertResult) -> None:
    """Proven indirectly: sign_document's own self-check uses the same
    validate_pdf_signature pyHanko call validate_signatures does, and a
    freshly signed document reports intact+valid immediately after -- see
    test_sign_produces_a_pades_signature. This test instead proves the
    *negative* half of the contract: a key that does not match the given
    certificate produces a signature pyHanko's own validator rejects, and
    sign_document refuses to hand that back as a success."""
    other = generate_self_signed_cert("Mismatched Key Owner")
    with pytest.raises(OpValidationError):
        sign_document(doc, cert_pem=cert.cert_pem, key_pem=other.key_pem)


@pytest.mark.feature("SIG-02", criterion=4)
def test_expired_certificate_is_refused_cleanly(doc: Document) -> None:
    expired = _expired_cert()
    before = doc.source_path.read_bytes()
    with pytest.raises(OpValidationError, match="expired"):
        sign_document(doc, cert_pem=expired.cert_pem, key_pem=expired.key_pem)
    assert doc.source_path.read_bytes() == before  # refused before anything was written


@pytest.mark.feature("SIG-02", criterion=4)
def test_malformed_certificate_is_refused_cleanly(doc: Document, cert: CertResult) -> None:
    with pytest.raises(OpValidationError):
        sign_document(doc, cert_pem="not a certificate", key_pem=cert.key_pem)


@pytest.mark.feature("SIG-02", criterion=1)
def test_sign_document_op_round_trips_through_parse_op(doc: Document, cert: CertResult) -> None:
    op = parse_op({"op": "sign_document", "cert_pem": cert.cert_pem, "key_pem": cert.key_pem, "reason": "approved"})
    assert isinstance(op, SignDocumentOp)
    result = op.apply(doc)
    assert result.field_name


# -- SIG-03: validation report ------------------------------------------------


@pytest.mark.feature("SIG-03", criterion=1)
def test_validation_report_covers_modification_chain_and_timestamp(doc: Document, cert: CertResult) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    reports = validate_signatures(doc)
    assert len(reports) == 1
    report = reports[0]
    assert report.modified_after_signing is False
    assert report.cert_valid_at_signing_time is True
    assert report.has_trusted_timestamp is False  # no tsa_url was used


@pytest.mark.feature("SIG-03", criterion=2)
def test_document_modified_after_signing_is_flagged_invalid(doc: Document, cert: CertResult) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    # An ordinary edit, made and saved *after* signing (not through sign_document):
    # a ordinary pymupdf incremental append, same as any further edit+save would do.
    live = pymupdf.open(doc.source_path)
    live[0].insert_text((72, 300), "a later edit")
    live.saveIncr()
    live.close()

    reports = validate_signatures(doc.source_path)
    assert len(reports) == 1
    assert reports[0].modified_after_signing is True
    assert reports[0].valid is False  # not silently reported as still valid
    assert reports[0].signature_valid is True  # the signature itself still checks out cryptographically


@pytest.mark.feature("SIG-03", criterion=3)
def test_multiple_signatures_each_report_their_own_revision(doc: Document, cert: CertResult) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem, field_name="Signature1")
    cert2 = generate_self_signed_cert("Countersigner")
    sign_document(doc, cert_pem=cert2.cert_pem, key_pem=cert2.key_pem, field_name="Signature2")

    reports = validate_signatures(doc)
    assert {r.field_name for r in reports} == {"Signature1", "Signature2"}
    by_name = {r.field_name: r for r in reports}
    assert by_name["Signature1"].signed_revision < by_name["Signature2"].signed_revision
    # The first signature's own file region was covered when it was made, but the
    # document has since grown (the countersignature), so it's no longer "entire_file".
    assert by_name["Signature1"].modified_after_signing is True
    assert by_name["Signature2"].modified_after_signing is False
    assert by_name["Signature2"].valid is True


@pytest.mark.feature("SIG-03", criterion=4)
def test_validation_needs_no_network_by_default(
    doc: Document, cert: CertResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No *outbound connection* is made -- not "no socket object is ever
    constructed": pyHanko's validator runs its own asyncio event loop
    internally, and asyncio's Windows (Proactor) implementation opens a
    local self-pipe socket *pair* as ordinary event-loop plumbing, with no
    bytes going anywhere off-box; blocking `socket.socket` outright (as
    tests/engine/test_certs.py does for engine.certs, which never touches
    asyncio at all) would flag that harmless local pipe as a false positive
    here. So this blocks the actual network primitive instead: an outbound
    `connect`/`connect_ex` call."""
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guard(real: object):
        def _call(self: socket.socket, address: object, *args: object, **kwargs: object) -> object:
            host = address[0] if isinstance(address, tuple) else address
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError(f"validate_signatures tried to connect out to {address!r}")
            return real(self, address, *args, **kwargs)  # type: ignore[operator]

        return _call

    monkeypatch.setattr(socket.socket, "connect", _guard(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(real_connect_ex))
    reports = validate_signatures(doc)
    assert reports[0].valid is True


@pytest.mark.feature("SIG-03", criterion=1)
def test_validate_signatures_op_round_trips_through_parse_op(doc: Document, cert: CertResult) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    op = parse_op({"op": "validate_signatures"})
    assert isinstance(op, ValidateSignaturesOp)
    reports = op.apply(doc)
    assert len(reports) == 1


# -- SIG-04: RFC 3161 timestamps ----------------------------------------------


@pytest.mark.feature("SIG-04", criterion=1)
def test_tsa_url_embeds_a_timestamp(doc: Document, cert: CertResult, mock_tsa: _MockTsa) -> None:
    result = sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem, tsa_url=mock_tsa.url)
    assert result.has_timestamp is True
    assert result.pades_level == "B-T"


@pytest.mark.feature("SIG-04", criterion=2)
def test_report_distinguishes_trusted_timestamp_from_signer_claimed_time(
    doc: Document, cert: CertResult, mock_tsa: _MockTsa
) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem, tsa_url=mock_tsa.url)
    reports = validate_signatures(doc, ts_trust_anchors=[mock_tsa.cert.cert_pem])
    assert len(reports) == 1
    report = reports[0]
    assert report.has_trusted_timestamp is True
    assert report.timestamp_time is not None
    assert report.signer_reported_time is not None
    assert isinstance(report.timestamp_time, _dt.datetime)
    assert isinstance(report.signer_reported_time, _dt.datetime)


@pytest.mark.feature("SIG-04", criterion=2)
def test_timestamp_self_trusted_by_default_with_no_trust_anchors_given(
    doc: Document, cert: CertResult, mock_tsa: _MockTsa
) -> None:
    """Without an explicit ts_trust_anchors, the TSA's own certificate (embedded
    in the timestamp token itself) is self-trusted -- the same default rule
    SIG-03's signer-certificate trust uses (module docstring)."""
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem, tsa_url=mock_tsa.url)
    reports = validate_signatures(doc)
    assert reports[0].has_trusted_timestamp is True


@pytest.mark.feature("SIG-04", criterion=3)
def test_unreachable_tsa_fails_the_whole_signing_operation_cleanly_and_fast(
    doc: Document, cert: CertResult, unreachable_tsa_url: str
) -> None:
    import time

    before = doc.source_path.read_bytes()
    started = time.monotonic()
    with pytest.raises(OpValidationError):
        sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem, tsa_url=unreachable_tsa_url)
    elapsed = time.monotonic() - started
    assert elapsed < 15.0  # fails fast, not a hang
    # Never silently completed without the timestamp that was requested: the file
    # on disk is untouched, not a signature missing only the timestamp.
    assert doc.source_path.read_bytes() == before
    assert validate_signatures(doc) == []


# -- SIG-05: signed-document warning and incremental-save mode ---------------


@pytest.mark.feature("SIG-05", criterion=1)
def test_document_signature_status_before_and_after_signing(doc: Document, cert: CertResult) -> None:
    status_before = document_signature_status(doc)
    assert status_before.has_signature is False
    assert status_before.is_still_valid is True  # vacuously: nothing to invalidate

    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    status_after = document_signature_status(doc)
    assert status_after.has_signature is True
    assert status_after.is_still_valid is True
    assert status_after.signature_count == 1


@pytest.mark.feature("SIG-05", criterion=2)
def test_save_after_signing_defaults_to_incremental_and_preserves_signed_bytes(doc: Document, cert: CertResult) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    signed_bytes = doc.source_path.read_bytes()

    doc.raw[0].insert_text((72, 400), "an edit after signing")
    save_result = doc.save(overwrite=True)  # mode="auto" (the default)

    assert save_result.mode == "incremental"
    saved_bytes = doc.source_path.read_bytes()
    assert saved_bytes[: len(signed_bytes)] == signed_bytes  # the signed revision is untouched


@pytest.mark.feature("SIG-05", criterion=3)
def test_editing_after_signing_is_recorded_as_invalidating_the_signature(doc: Document, cert: CertResult) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    assert document_signature_status(doc).is_still_valid is True

    doc.raw[0].insert_text((72, 400), "proceeding with an edit anyway")
    doc.save(overwrite=True)

    status = document_signature_status(doc)
    assert status.has_signature is True
    assert status.is_still_valid is False  # the edit is recorded as having invalidated it
    reports = validate_signatures(doc)
    assert reports[0].modified_after_signing is True
    assert reports[0].valid is False


@pytest.mark.feature("SIG-05", criterion=1)
def test_document_signature_status_op_round_trips_through_parse_op(doc: Document, cert: CertResult) -> None:
    sign_document(doc, cert_pem=cert.cert_pem, key_pem=cert.key_pem)
    op = parse_op({"op": "document_signature_status"})
    assert isinstance(op, DocumentSignatureStatusOp)
    status = op.apply(doc)
    assert status.has_signature is True
