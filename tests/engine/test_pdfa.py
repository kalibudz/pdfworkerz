"""CVF-07: PDF to PDF/A through Ghostscript, checked, and validated by veraPDF when present."""

from __future__ import annotations

import io
import stat
import sys
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.document import Document
from engine.errors import MissingToolError
from engine.external import find_tool
from engine.pdfa import convert_to_pdfa
from tests import pdfs

needs_ghostscript = pytest.mark.skipif(find_tool("ghostscript") is None, reason="Ghostscript is not installed")


@pytest.fixture(scope="module")
def report() -> Document:
    return Document.from_bytes(pdfs.report_pdf())


@needs_ghostscript
@pytest.mark.feature("CVF-07", criterion=1)
@pytest.mark.parametrize("level", ["1b", "2b", "3b"])
def test_the_result_declares_its_pdfa_level_in_xmp(report: Document, level: str) -> None:
    result = convert_to_pdfa(report, level=level)  # type: ignore[arg-type]
    assert result.declares_level and result.level == level
    with pikepdf.open(io.BytesIO(result.data)) as pdf, pdf.open_metadata() as meta:
        assert meta["pdfaid:part"] == level[0]
        assert meta["pdfaid:conformance"].upper() == "B"
    assert "Quarterly Report" in pymupdf.open("pdf", result.data)[0].get_text("text"), "the content survives"


@needs_ghostscript
@pytest.mark.feature("CVF-07", criterion=2)
def test_the_result_has_an_output_intent_with_a_profile_and_embeds_its_fonts(report: Document) -> None:
    result = convert_to_pdfa(report)
    assert result.has_output_intent and result.fonts_embedded
    converted = pymupdf.open("pdf", result.data)
    for page in converted:
        for font in page.get_fonts():
            assert font[1] != "n/a", f"{font[3]} is not embedded"
    with pikepdf.open(io.BytesIO(result.data)) as pdf:
        profile = pdf.Root.OutputIntents[0].DestOutputProfile
        assert len(profile.read_bytes()) > 1000


def _fake_verapdf(folder: Path, verdict: str) -> Path:
    if sys.platform == "win32":
        script = folder / "verapdf.bat"
        script.write_text(f"@echo off\r\necho {verdict} %~nx5 2b\r\n")
    else:
        script = folder / "verapdf"
        script.write_text(f"#!/bin/sh\necho '{verdict} '$(basename \"$5\")' 2b'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@needs_ghostscript
@pytest.mark.feature("CVF-07", criterion=3)
@pytest.mark.parametrize(("verdict", "valid"), [("PASS", True), ("FAIL", False)])
def test_verapdf_is_run_and_its_verdict_reported(
    report: Document, work_dir: Path, monkeypatch: pytest.MonkeyPatch, verdict: str, valid: bool
) -> None:
    monkeypatch.setenv("PDFWORKERZ_VERAPDF", str(_fake_verapdf(work_dir, verdict)))
    result = convert_to_pdfa(report)
    assert result.validated_by == "veraPDF" and result.valid is valid
    assert any(verdict in note for note in result.notes)


@needs_ghostscript
@pytest.mark.feature("CVF-07", criterion=3)
def test_without_verapdf_the_result_says_it_was_not_independently_validated(
    report: Document, monkeypatch: pytest.MonkeyPatch, work_dir: Path
) -> None:
    monkeypatch.setenv("PDFWORKERZ_VERAPDF", str(work_dir / "absent"))
    result = convert_to_pdfa(report)
    assert result.validated_by is None and result.valid is None
    assert any("not independently validated" in note for note in result.notes)


@pytest.mark.feature("CVF-07", criterion=4)
def test_missing_ghostscript_is_named_with_how_to_install_it(
    report: Document, monkeypatch: pytest.MonkeyPatch, work_dir: Path
) -> None:
    monkeypatch.setenv("PDFWORKERZ_GHOSTSCRIPT", str(work_dir / "absent"))
    with pytest.raises(MissingToolError, match=r"Ghostscript is not installed.*PDF/A"):
        convert_to_pdfa(report)
