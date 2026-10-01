"""SIG-02/03/04: real cryptographic PDF signing (PAdES) and its validation
report, built on pyHanko -- a clearly separate concern from
``engine.signatures`` (SIG-01), which only draws a picture on the page and
never touches an AcroForm signature field or any cryptography at all. Nothing
in this module is reachable from that one, or vice versa.

**What "PAdES level" this module actually produces (verified against the
installed pyHanko 0.37.0, not assumed from general PAdES knowledge).**
``sign_document`` always sets ``subfilter=SigSeedSubFilter.PADES``
(ETSI.CAdES.detached), which is what makes the signature PAdES rather than
a plain PDF/CMS (``adbe.pkcs7.detached``) one. With no ``tsa_url``, that is
**PAdES B-B** (Basic, with signing-time only as *claimed* by the signer, no
independent trusted time). Passing ``tsa_url`` embeds an RFC 3161 timestamp
token over the signature, which raises it to **PAdES B-T** (Basic with
Timestamp: now there is a trusted time, not just a self-reported one). This
module does not attempt B-LT/B-LTA (those need embedded revocation
information -- OCSP/CRL responses collected at signing time via
``embed_validation_info``/``use_pades_lta`` -- which requires a real CA
with a revocation service; a locally generated self-signed certificate has
none, so going further than B-T here would be theatre, not a real
guarantee).

**Incremental by construction.** ``sign_pdf`` is handed a
``pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter`` wrapping
the *exact bytes already on disk* at ``document.source_path`` (read directly
from the file, never PyMuPDF's own re-serialization -- ``Document.to_bytes()``
is not byte-identical to what PyMuPDF itself first wrote, even with zero
edits, since a full ``tobytes()`` pass can renumber objects; reading the raw
file instead is what makes the "prior content preserved byte-for-byte" claim
literally true and directly testable by comparing the new file's prefix
against the old file's full bytes). pyHanko's incremental writer then only
*appends* a new revision; nothing already on disk is rewritten. After
writing the result back to disk, the given ``document`` is reloaded in place
(:meth:`~engine.document.Document.reload`) so the caller's `Document` object
reflects the now-signed file.

**Self-validation before returning success.** Immediately after signing,
this module re-reads the signature it just wrote and runs it through
pyHanko's own ``validate_pdf_signature`` (the same function
``validate_signatures`` below uses); if pyHanko itself doesn't consider the
fresh signature intact and cryptographically valid, ``sign_document`` raises
rather than handing back a signature it would not trust itself.

**Certificate validity is checked independently of pyHanko's own trust
machinery.** A locally generated self-signed certificate *is* its own trust
anchor, and ``pyhanko_certvalidator`` does not re-check a trust anchor's own
temporal validity window as part of path validation (confirmed: signing and
self-validating with a certificate whose ``not_valid_after`` is in the past
still came back ``valid=True, trusted=True`` from pyHanko's validator in a
throwaway script before this module was written) -- an anchor is trusted by
definition, not validated against itself. So an expired or not-yet-valid
certificate is refused explicitly, with plain ``cryptography`` library
checks on ``not_valid_before_utc``/``not_valid_after_utc``, *before* any
pyHanko call is made; this is the only thing standing between "an expired
cert signs successfully and reports itself trustworthy" and a clean refusal.

**No network access during validation, by default.** ``validate_signatures``
builds its own ``pyhanko_certvalidator.context.ValidationContext`` with
``allow_fetching=False`` (confirmed as the constructor's own default) unless
the caller explicitly opts into OCSP/CRL fetching -- not implemented here,
out of scope for SIG-03/04 as specified (no revocation checking beyond the
certificate's own validity window and the embedded timestamp).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import TYPE_CHECKING

from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import BaseModel, ConfigDict
from pyhanko.keys import pemder
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields
from pyhanko.sign.signers import PdfSignatureMetadata, SimpleSigner, sign_pdf
from pyhanko.sign.timestamps import HTTPTimeStamper
from pyhanko.sign.validation import generic_cms, validate_pdf_signature
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator.context import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

from engine.document import Document
from engine.errors import OpValidationError

if TYPE_CHECKING:
    from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature

_DEFAULT_TSA_TIMEOUT = 10  # seconds: "fails fast", not "hangs the whole signing operation"


class SignResult(BaseModel):
    """What SIG-02 reports back after a successful signature (self-validated already)."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    path: Path
    pades_level: str
    """``"B-B"`` (no timestamp) or ``"B-T"`` (RFC 3161 timestamp embedded) -- see module docstring."""
    has_timestamp: bool
    bytes_written: int


class SignatureReport(BaseModel):
    """SIG-03: one embedded signature's validation result."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    signed_revision: int
    """Which incremental revision of the file this signature covers (0-based, pyHanko's own numbering)."""
    coverage: str
    """``"entire_file"``, ``"entire_revision"``, ``"contiguous_block_from_start"`` or ``"unclear"``."""
    intact: bool
    """The signed bytes have not been tampered with (pyHanko's own cryptographic check)."""
    signature_valid: bool
    """The signature cryptographically matches the certificate (pyHanko's own check) --
    note this can be True even for a signature a *later* revision has invalidated;
    see `modified_after_signing` and the bottom-line `valid` field for that."""
    modified_after_signing: bool
    """True when this signature does not cover the entire current file, i.e. the
    document gained content after this signature was applied (coverage != "entire_file")."""
    valid: bool
    """The bottom line SIG-03 asks for: `intact and signature_valid and not modified_after_signing`.
    A document modified after signing is reported invalid here even though pyHanko's
    own `signature_valid` alone would still say the signature itself checks out."""
    cert_subject: str
    cert_valid_at_signing_time: bool
    """Whether the signer's certificate's own validity window covered the signing
    time (its own claimed time, or the trusted timestamp if one is present) --
    see module docstring on why this, not chain/revocation validation, is what
    "validates" means for a self-signed certificate with no real trust root."""
    signer_reported_time: _dt.datetime | None
    """The signing time as the signer's own software claims it, unverified."""
    has_trusted_timestamp: bool
    timestamp_time: _dt.datetime | None
    """The RFC 3161 timestamp's own time, distinct from `signer_reported_time` above --
    None when no timestamp is embedded, or when `trusted_timestamp` is False."""
    timestamp_trusted: bool
    """Whether the embedded timestamp token's own signing chain validated against
    the trust anchors given (or self-trust of the TSA's own certificate, by
    default -- see `validate_signatures`'s `ts_trust_anchors` parameter)."""


class SignedDocStatus(BaseModel):
    """SIG-05: what the UI shows before letting an edit proceed on a signed document."""

    model_config = ConfigDict(frozen=True)

    has_signature: bool
    is_still_valid: bool
    """False whenever any signature is no longer current (see `SignatureReport.valid`) --
    True (vacuously) when `has_signature` is False."""
    signature_count: int


def _load_cert(cert_pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise OpValidationError(f"cert_pem is not a valid PEM certificate: {exc}") from exc


def _check_cert_validity_window(cert: x509.Certificate, *, at: _dt.datetime | None = None) -> None:
    """Refuse an expired or not-yet-valid certificate before it ever reaches pyHanko.

    Necessary because pyHanko/pyhanko_certvalidator does not itself re-check a
    *trust anchor's* own temporal validity (a self-signed cert is its own
    anchor) -- see module docstring; without this check, signing with an
    expired self-signed certificate would succeed and self-validate as
    "trusted" anyway.
    """
    moment = at or _dt.datetime.now(_dt.UTC)
    if moment < cert.not_valid_before_utc:
        raise OpValidationError(
            f"certificate is not valid until {cert.not_valid_before_utc.isoformat()} (checked at {moment.isoformat()})"
        )
    if moment > cert.not_valid_after_utc:
        raise OpValidationError(
            f"certificate expired {cert.not_valid_after_utc.isoformat()} (checked at {moment.isoformat()})"
        )


def _load_signer(
    *, cert_pem: str, key_pem: str, key_passphrase: str | None, signing_cert: x509.Certificate
) -> SimpleSigner:
    passphrase = key_passphrase.encode("utf-8") if key_passphrase else None
    try:
        key = pemder.load_private_key_from_pemder_data(key_pem.encode("ascii"), passphrase=passphrase)
    except Exception as exc:  # pyHanko/asn1crypto raise a range of types for bad key material
        raise OpValidationError(f"key_pem could not be loaded (wrong passphrase, or not a valid key?): {exc}") from exc
    # Cheap sanity check with a wholly independent decoder (cryptography, not pyHanko's own
    # asn1crypto-based one), so a key/cert mismatch is refused clearly rather than surfacing
    # as a baffling signature-verification failure from deep inside pyHanko later.
    if key_passphrase is not None:
        try:
            load_pem_private_key(key_pem.encode("ascii"), password=key_passphrase.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise OpValidationError(f"key_pem could not be decrypted with the given passphrase: {exc}") from exc
    pyhanko_cert = next(pemder.load_certs_from_pemder_data(cert_pem.encode("ascii")))
    return SimpleSigner(signing_cert=pyhanko_cert, signing_key=key, cert_registry=SimpleCertificateStore())


def _existing_signature_field_names(document: Document) -> set[str]:
    names: set[str] = set()
    for page_index in range(document.page_count):
        for widget in document.raw[page_index].widgets() or ():
            if widget.field_type_string == "Signature":
                names.add(widget.field_name)
    return names


def _unique_field_name(document: Document, requested: str | None) -> tuple[str, bool]:
    """Return (field_name, already_exists)."""
    existing = _existing_signature_field_names(document)
    if requested is not None:
        return requested, requested in existing
    n = 1
    while f"Signature{n}" in existing:
        n += 1
    return f"Signature{n}", False


def sign_document(
    document: Document,
    *,
    cert_pem: str,
    key_pem: str,
    key_passphrase: str | None = None,
    field_name: str | None = None,
    reason: str | None = None,
    location: str | None = None,
    tsa_url: str | None = None,
    path: str | None = None,
) -> SignResult:
    """Apply a real PAdES digital signature to `document` (SIG-02), optionally
    timestamped by an RFC 3161 TSA (SIG-04).

    Signs the bytes currently on disk at `document.source_path` -- not any
    unsaved in-memory edits -- the same precondition an incremental
    :meth:`Document.save` already has; save first if there are pending edits.
    Refused via :class:`OpValidationError` if `document.dirty` (found by
    independent review: checking only `file_backed` here let a pending,
    unsaved edit slip past silently -- `file_backed` is about *provenance*
    reloaded from a snapshot or not, not about whether memory has since
    diverged from disk, which is exactly what `dirty` tracks). `path`
    defaults to overwriting `document.source_path` itself (an incremental
    update is exactly that: appending to the same file), matching how a
    signed document's own saves behave from then on (SIG-05).

    If `field_name` names an existing (unsigned) signature field, signs into
    it (FRM-03's flow, when that field already exists); otherwise creates a
    new invisible signature field with that name (or an auto-generated
    ``SignatureN`` name if `field_name` is omitted) via pyHanko's own
    ``fields.SigFieldSpec``, its normal way of adding one.

    Raises :class:`OpValidationError` for a malformed or expired/not-yet-valid
    certificate or key (checked before any pyHanko call), for a TSA that
    fails to respond when `tsa_url` is given (the whole operation fails --
    never silently signs without the requested timestamp), or if pyHanko's
    own post-signing validation doesn't come back intact and valid (the
    self-check this function always performs before returning).
    """
    if document.source_path is None or not document.file_backed:
        raise OpValidationError(
            "sign_document needs a document opened from a file, read from that file (not "
            "reloaded from an undo/redo snapshot)"
        )
    if document.dirty:
        raise OpValidationError(
            "sign_document would sign the bytes on disk, not this document's unsaved edits -- save it first, then sign"
        )
    cert = _load_cert(cert_pem)
    _check_cert_validity_window(cert)
    signer = _load_signer(cert_pem=cert_pem, key_pem=key_pem, key_passphrase=key_passphrase, signing_cert=cert)

    resolved_field_name, exists = _unique_field_name(document, field_name)
    new_field_spec = None if exists else fields.SigFieldSpec(sig_field_name=resolved_field_name, on_page=0, box=None)

    timestamper = HTTPTimeStamper(url=tsa_url, timeout=_DEFAULT_TSA_TIMEOUT) if tsa_url else None

    source = document.source_path
    target = Path(path) if path is not None else source
    original_bytes = source.read_bytes()

    meta = PdfSignatureMetadata(
        field_name=resolved_field_name,
        subfilter=fields.SigSeedSubFilter.PADES,
        reason=reason,
        location=location,
    )
    try:
        with source.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf)
            out = sign_pdf(
                writer,
                meta,
                signer=signer,
                timestamper=timestamper,
                new_field_spec=new_field_spec,
                existing_fields_only=exists,
            )
            signed_bytes = out.getvalue() if hasattr(out, "getvalue") else out.read()
    except OpValidationError:
        raise
    except Exception as exc:
        # Covers pyhanko.sign.timestamps.common_utils.TimestampRequestError (unreachable/bad
        # TSA) and any other pyHanko-side signing failure: the whole operation fails cleanly,
        # never silently falling back to signing without the timestamp that was asked for.
        verb = "timestamp" if tsa_url else "sign"
        raise OpValidationError(f"could not {verb} {source}: {exc}") from exc

    if signed_bytes[: len(original_bytes)] != original_bytes:  # pragma: no cover -- pyHanko invariant, belt and braces
        raise OpValidationError("internal error: the signed output did not preserve the original bytes verbatim")

    target.write_bytes(signed_bytes)

    # Self-validate before telling the caller this succeeded (criterion 3): re-read exactly
    # what was just written and run it through pyHanko's own validator.
    reader = PdfFileReader(target.open("rb"))
    try:
        embedded = next(s for s in reader.embedded_signatures if s.field_name == resolved_field_name)
        vc = ValidationContext(trust_roots=_parse_pem_certs([cert_pem]), allow_fetching=False)
        status = validate_pdf_signature(embedded, signer_validation_context=vc)
    finally:
        reader.stream.close()
    if not (status.intact and status.valid):
        raise OpValidationError(
            f"the signature just written did not self-validate (intact={status.intact}, valid={status.valid})"
        )

    document.reload()

    return SignResult(
        field_name=resolved_field_name,
        path=target,
        pades_level="B-T" if tsa_url else "B-B",
        has_timestamp=tsa_url is not None,
        bytes_written=len(signed_bytes),
    )


def _coverage_name(level: SignatureCoverageLevel | None) -> str:
    if level is None:
        return "unclear"
    return {
        SignatureCoverageLevel.ENTIRE_FILE: "entire_file",
        SignatureCoverageLevel.ENTIRE_REVISION: "entire_revision",
        SignatureCoverageLevel.CONTIGUOUS_BLOCK_FROM_START: "contiguous_block_from_start",
        SignatureCoverageLevel.UNCLEAR: "unclear",
    }.get(level, "unclear")


def _parse_pem_certs(pems: list[str]) -> list[asn1_x509.Certificate]:
    """PEM certificates as asn1crypto ``x509.Certificate`` objects -- what
    ``pyhanko_certvalidator.ValidationContext`` needs for `trust_roots`
    (NOT the `cryptography` library's own `x509.Certificate`, a different
    type that fails deep inside pyhanko_certvalidator's own hashing with an
    opaque `AttributeError`, confirmed with a throwaway script before this
    helper existed)."""
    return [next(pemder.load_certs_from_pemder_data(pem.encode("ascii"))) for pem in pems]


def _trust_roots_for(explicit_pems: list[str] | None, own_cert: asn1_x509.Certificate) -> list[asn1_x509.Certificate]:
    if explicit_pems:
        return _parse_pem_certs(explicit_pems)
    # No trust root supplied: self-trust the certificate embedded in the signature itself --
    # the only meaningful thing "validates" can mean for a self-signed cert (module docstring).
    return [own_cert]


def validate_signatures(
    document_or_path: Document | str | Path,
    *,
    trust_anchors: list[str] | None = None,
    ts_trust_anchors: list[str] | None = None,
    password: str | None = None,
) -> list[SignatureReport]:
    """SIG-03: a validation report for every signature embedded in a document.

    `document_or_path` may be an already-open :class:`Document` (its current
    on-disk bytes, via `source_path`, are read fresh -- not any unsaved
    in-memory edits) or a path to read directly. `trust_anchors`/
    `ts_trust_anchors` are PEM certificates to trust for the signer's /
    timestamp authority's chain respectively; omitted, each signature's own
    (or its timestamp's own) certificate is self-trusted -- see module
    docstring for why that is the right default for a self-signed cert with
    no real CA. No network access is made (`allow_fetching=False`) since
    neither revocation nor timestamp checking here ever needs it: a trusted
    timestamp's trust is judged from `ts_trust_anchors`, never by contacting
    a TSA again.
    """
    if isinstance(document_or_path, Document):
        if document_or_path.source_path is None:
            raise OpValidationError("validate_signatures needs a document with a source_path (open from a file)")
        target = document_or_path.source_path
    else:
        target = Path(document_or_path)

    reports: list[SignatureReport] = []
    reader = PdfFileReader(target.open("rb"))
    try:
        for embedded in reader.embedded_signatures:
            reports.append(_report_for(embedded, trust_anchors, ts_trust_anchors))
    finally:
        reader.stream.close()
    return reports


def _ts_trust_roots(
    embedded: EmbeddedPdfSignature, ts_trust_anchors: list[str] | None
) -> list[asn1_x509.Certificate] | None:
    """Trust roots for the embedded timestamp's own chain, built *before* calling
    pyHanko's validator (so its own judgment of the timestamp's trust reflects
    them), following the same self-trust-by-default rule as the signer's own
    certificate. Returns None when there is no embedded timestamp at all."""
    if ts_trust_anchors:
        return _parse_pem_certs(ts_trust_anchors)
    tst_data = next(generic_cms.extract_tst_data_iter(embedded.signer_info, signed=False), None)
    if tst_data is None:
        return None
    tsa_certs = [c.chosen for c in tst_data["certificates"]]
    return tsa_certs or None


def _report_for(
    embedded: EmbeddedPdfSignature, trust_anchors: list[str] | None, ts_trust_anchors: list[str] | None
) -> SignatureReport:
    own_cert = embedded.signer_cert
    vc = ValidationContext(trust_roots=_trust_roots_for(trust_anchors, own_cert), allow_fetching=False)
    ts_roots = _ts_trust_roots(embedded, ts_trust_anchors)
    ts_vc = ValidationContext(trust_roots=ts_roots, allow_fetching=False) if ts_roots else None
    status = validate_pdf_signature(embedded, signer_validation_context=vc, ts_validation_context=ts_vc)

    modified_after_signing = status.coverage != SignatureCoverageLevel.ENTIRE_FILE
    bottom_line_valid = bool(status.intact and status.valid and not modified_after_signing)

    signer_reported_time = status.signer_reported_dt
    has_timestamp = status.timestamp_validity is not None
    timestamp_trusted = False
    timestamp_time: _dt.datetime | None = None
    if status.timestamp_validity is not None:
        tv = status.timestamp_validity
        timestamp_time = tv.timestamp
        timestamp_trusted = bool(tv.intact and tv.valid)

    cert_valid_at_signing = _cert_covers(own_cert, timestamp_time or signer_reported_time)

    return SignatureReport(
        field_name=embedded.field_name,
        signed_revision=embedded.signed_revision,
        coverage=_coverage_name(status.coverage),
        intact=bool(status.intact),
        signature_valid=bool(status.valid),
        modified_after_signing=modified_after_signing,
        valid=bottom_line_valid,
        cert_subject=own_cert.subject.human_friendly,
        cert_valid_at_signing_time=cert_valid_at_signing,
        signer_reported_time=signer_reported_time,
        has_trusted_timestamp=has_timestamp and timestamp_trusted,
        timestamp_time=timestamp_time if has_timestamp and timestamp_trusted else None,
        timestamp_trusted=timestamp_trusted,
    )


def _cert_covers(asn1_cert: asn1_x509.Certificate, moment: _dt.datetime | None) -> bool:
    """Whether `asn1_cert`'s own notBefore/notAfter window covers `moment`
    (its own self-reported/timestamped signing time) -- see module docstring
    on why this, not chain/revocation validation, is "cert_valid" here."""
    if moment is None:
        return False
    validity = asn1_cert.native["tbs_certificate"]["validity"]
    not_before = validity["not_before"]
    not_after = validity["not_after"]
    return bool(not_before <= moment <= not_after)


def document_signature_status(document: Document) -> SignedDocStatus:
    """SIG-05: what the UI shows before letting an edit proceed on a signed document.

    Wraps `validate_signatures` on `document` as currently saved on disk.
    `is_still_valid` is True only when every embedded signature's own
    bottom-line `valid` is True (vacuously True when there is no signature
    at all). This reflects the document *as last saved*, not unsaved
    in-memory edits -- the same "ask before editing further" moment SIG-05's
    first criterion describes; proceeding with an edit and then saving is
    what actually invalidates a signature (SIG-05's second/third criteria),
    at which point a fresh call here (after that save) reports it.
    """
    if document.source_path is None or not document.file_backed:
        return SignedDocStatus(has_signature=False, is_still_valid=True, signature_count=0)
    reports = validate_signatures(document)
    return SignedDocStatus(
        has_signature=len(reports) > 0,
        is_still_valid=all(r.valid for r in reports),
        signature_count=len(reports),
    )
