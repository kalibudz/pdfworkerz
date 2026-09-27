"""Golden PDF test corpus generator (INF-06).

Every file here is generated from scratch by PyMuPDF and pikepdf, so
nothing is downloaded and every file's ground truth (page count, text,
password, permissions) is known exactly by the code that built it. Files
are written to ``tests/corpus/generated/`` (git-ignored) and rebuilt only
when missing, so the suite stays fast on repeat runs.

This corpus is deliberately small in P1: fonts, encryption revisions,
repair and structural-detection cases needed by the P1 engine. It grows in
later phases as their features need more (a CJK/CID file for FNT-14, a
Type3 file for FNT-15, and so on) -- see SPEC.md section 12.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pikepdf
import pymupdf

OUT_DIR = Path(__file__).resolve().parent / "generated"

USER_PASSWORD = "user-pw"
OWNER_PASSWORD = "owner-pw"


@dataclass(frozen=True)
class Corpus:
    """Paths to every generated fixture, plus the ground truth needed to check them."""

    simple: Path
    multi_page: Path
    multi_page_count: int
    large: Path
    large_page_count: int
    encrypted_rc4_40: Path
    encrypted_rc4_128: Path
    encrypted_aes_128: Path
    encrypted_aes_256: Path
    owner_only: Path
    broken_truncated: Path
    broken_severely: Path
    certificate_stub: Path
    form_and_signature: Path
    layered: Path
    user_password: str = USER_PASSWORD
    owner_password: str = OWNER_PASSWORD


def _simple(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello, PDFWorkerz.", fontsize=14)
    doc.save(path)
    doc.close()


def _multi_page(path: Path, count: int) -> None:
    doc = pymupdf.open()
    for i in range(count):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} of {count}", fontsize=12)
    doc.save(path)
    doc.close()


def _encrypted(path: Path, *, r: int, aes: bool, metadata: bool = True) -> None:
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    del page  # unused; add_blank_page() both adds and returns the page
    pdf.save(
        path,
        encryption=pikepdf.Encryption(owner=OWNER_PASSWORD, user=USER_PASSWORD, R=r, aes=aes, metadata=metadata),
    )
    pdf.close()


def _owner_only(path: Path) -> None:
    """A file with no user password but a restrictive owner password (SEC-02)."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(
        path,
        encryption=pikepdf.Encryption(
            owner=OWNER_PASSWORD,
            user="",
            R=6,
            aes=True,
            allow=pikepdf.Permissions(
                accessibility=True,
                extract=False,
                modify_annotation=False,
                modify_assembly=False,
                modify_form=False,
                modify_other=False,
                print_lowres=True,
                print_highres=False,
            ),
        ),
    )
    pdf.close()


def _broken_truncated(path: Path, source: Path) -> None:
    """A file missing its tail (OPT-06): the common 'download/upload cut off' case.

    Dropping the last 10% of bytes removes the xref table and trailer while
    leaving every object body intact, which MuPDF's repair can reconstruct
    from a linear object scan.
    """
    data = source.read_bytes()
    path.write_bytes(data[: -max(50, len(data) // 10)])


def _broken_severely(path: Path) -> None:
    """A file too damaged to repair (OPT-06): only the PDF header survives, no objects at all."""
    path.write_bytes(b"%PDF-1.7\n%\xc2\xb5\xc2\xb6\n")


def _certificate_stub(path: Path) -> None:
    """A file whose trailer announces Adobe.PubSec (public-key) encryption (SEC-07).

    This is NOT a working certificate-encrypted PDF -- its content streams
    are plain, unencrypted bytes. It exists only to exercise trailer-level
    detection of an encryption filter PDFWorkerz cannot decrypt, without
    needing a real certificate and recipient key pair. See
    engine.security.detect_certificate_encryption and SPEC.md section 6.

    Built as a hand-written PDF incremental update (ISO 32000-1 section
    7.5.6): the base file's bytes are kept as-is, and a new trailer +
    xref section is appended pointing at a new Encrypt object.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(path)
    pdf.close()

    data = path.read_bytes()
    with pikepdf.open(path) as base:
        root_ref = base.trailer["/Root"]
        root_obj_num, root_gen = root_ref.objgen
        size = int(base.trailer["/Size"])

    matches = list(re.finditer(rb"startxref\s+(\d+)\s+%%EOF", data))
    prev_startxref = int(matches[-1].group(1))

    new_obj_num = size
    encrypt_obj = (
        f"{new_obj_num} 0 obj\n<< /Filter /Adobe.PubSec /SubFilter /adbe.pkcs7.s4 /V 2 /R 3 >>\nendobj\n"
    ).encode("ascii")
    xref_offset = len(data) + len(encrypt_obj)
    xref_table = (f"xref\n0 1\n0000000000 65535 f \n{new_obj_num} 1\n{len(data):010d} 00000 n \n").encode("ascii")
    trailer = (
        f"trailer\n<< /Size {new_obj_num + 1} /Root {root_obj_num} {root_gen} R "
        f"/Prev {prev_startxref} /Encrypt {new_obj_num} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")

    path.write_bytes(data + encrypt_obj + xref_table + trailer)


def _form_and_signature(path: Path) -> None:
    """A page with a text field and a signature field (COR-03 has_forms/has_signatures)."""
    doc = pymupdf.open()
    page = doc.new_page()

    text_field = pymupdf.Widget()
    text_field.field_name = "full_name"
    text_field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    text_field.rect = pymupdf.Rect(72, 72, 300, 100)
    page.add_widget(text_field)

    signature_field = pymupdf.Widget()
    signature_field.field_name = "signature"
    signature_field.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    signature_field.rect = pymupdf.Rect(72, 120, 300, 170)
    page.add_widget(signature_field)

    doc.save(path)
    doc.close()


def _layered(path: Path) -> None:
    """A page with an optional-content group (COR-03 has_layers / future COR-12)."""
    doc = pymupdf.open()
    doc.new_page()
    doc.add_ocg("Annotations Layer", on=True)
    doc.save(path)
    doc.close()


def build_corpus(out_dir: Path = OUT_DIR, *, force: bool = False) -> Corpus:
    """Build (or reuse) every fixture and return their paths and ground truth."""
    out_dir.mkdir(parents=True, exist_ok=True)
    multi_page_count = 5
    large_page_count = 1000

    paths = {
        "simple": out_dir / "simple.pdf",
        "multi_page": out_dir / "multi_page.pdf",
        "large": out_dir / "large_1000.pdf",
        "encrypted_rc4_40": out_dir / "encrypted_rc4_40.pdf",
        "encrypted_rc4_128": out_dir / "encrypted_rc4_128.pdf",
        "encrypted_aes_128": out_dir / "encrypted_aes_128.pdf",
        "encrypted_aes_256": out_dir / "encrypted_aes_256.pdf",
        "owner_only": out_dir / "owner_only.pdf",
        "broken_truncated": out_dir / "broken_truncated.pdf",
        "broken_severely": out_dir / "broken_severely.pdf",
        "certificate_stub": out_dir / "certificate_stub.pdf",
        "form_and_signature": out_dir / "form_and_signature.pdf",
        "layered": out_dir / "layered.pdf",
    }

    if force or not paths["simple"].exists():
        _simple(paths["simple"])
    if force or not paths["multi_page"].exists():
        _multi_page(paths["multi_page"], multi_page_count)
    if force or not paths["large"].exists():
        _multi_page(paths["large"], large_page_count)
    if force or not paths["encrypted_rc4_40"].exists():
        _encrypted(paths["encrypted_rc4_40"], r=2, aes=False, metadata=False)
    if force or not paths["encrypted_rc4_128"].exists():
        _encrypted(paths["encrypted_rc4_128"], r=3, aes=False, metadata=False)
    if force or not paths["encrypted_aes_128"].exists():
        _encrypted(paths["encrypted_aes_128"], r=4, aes=True)
    if force or not paths["encrypted_aes_256"].exists():
        _encrypted(paths["encrypted_aes_256"], r=6, aes=True)
    if force or not paths["owner_only"].exists():
        _owner_only(paths["owner_only"])
    if force or not paths["broken_truncated"].exists():
        _broken_truncated(paths["broken_truncated"], paths["multi_page"])
    if force or not paths["broken_severely"].exists():
        _broken_severely(paths["broken_severely"])
    if force or not paths["certificate_stub"].exists():
        _certificate_stub(paths["certificate_stub"])
    if force or not paths["form_and_signature"].exists():
        _form_and_signature(paths["form_and_signature"])
    if force or not paths["layered"].exists():
        _layered(paths["layered"])

    return Corpus(
        simple=paths["simple"],
        multi_page=paths["multi_page"],
        multi_page_count=multi_page_count,
        large=paths["large"],
        large_page_count=large_page_count,
        encrypted_rc4_40=paths["encrypted_rc4_40"],
        encrypted_rc4_128=paths["encrypted_rc4_128"],
        encrypted_aes_128=paths["encrypted_aes_128"],
        encrypted_aes_256=paths["encrypted_aes_256"],
        owner_only=paths["owner_only"],
        broken_truncated=paths["broken_truncated"],
        broken_severely=paths["broken_severely"],
        certificate_stub=paths["certificate_stub"],
        form_and_signature=paths["form_and_signature"],
        layered=paths["layered"],
    )


if __name__ == "__main__":
    corpus = build_corpus(force=True)
    print(f"Corpus written to {OUT_DIR}")
