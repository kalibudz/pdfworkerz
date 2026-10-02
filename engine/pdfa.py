"""CVF-07: convert a PDF to PDF/A, the archival profile (every font embedded, colour defined,
nothing that depends on the outside world).

Ghostscript does the conversion with an sRGB output intent. The result is then *checked* here
(it declares its level in XMP, carries the output intent, embeds its fonts), and, when veraPDF
is installed, validated by it -- an independent verdict from the reference validator. When
veraPDF is not installed the result says it was *not* independently validated, rather than
implying a pass.
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pikepdf

from engine.document import Document
from engine.errors import ConversionError
from engine.external import find_tool, require_tool, run_tool

Level = Literal["1b", "2b", "3b"]
_PART = {"1b": 1, "2b": 2, "3b": 3}
_VERSIONED_SHARES = ("/usr/share/ghostscript", "/opt/homebrew/share/ghostscript", "/usr/local/share/ghostscript")
_PDFA_DEF = """%!
/ICCProfile ({profile}) def
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} <</N 3>> /PUT pdfmark
[{{icc_PDFA}} ICCProfile (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} <<
  /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB) /Info (sRGB) /RegistryName (http://www.color.org)
>> /PUT pdfmark
[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFA}} ]>> /PUT pdfmark
"""


@dataclass(frozen=True)
class PdfAResult:
    data: bytes
    level: Level
    declares_level: bool
    """The XMP metadata says the file is PDF/A of this level."""
    has_output_intent: bool
    fonts_embedded: bool
    validated_by: str | None
    """"veraPDF" when it ran, else None -- the file was only checked by the steps above."""
    valid: bool | None
    """veraPDF's verdict; None when it did not run."""
    notes: list[str] = field(default_factory=list)


def _profile_folders(ghostscript: Path) -> Iterator[Path]:
    yield (
        ghostscript.parent.parent / "iccprofiles"
    )  # the Windows layout: <install>/bin/gs.exe beside <install>/iccprofiles
    yield Path("/usr/share/color/icc/ghostscript")
    for base in _VERSIONED_SHARES:
        yield from sorted(Path(base).glob("*/iccprofiles"), reverse=True)


def _srgb_profile(ghostscript: Path) -> Path:
    for folder in _profile_folders(ghostscript):
        if (folder / "srgb.icc").is_file():
            return folder / "srgb.icc"
    raise ConversionError("Ghostscript's sRGB colour profile (srgb.icc) could not be found, so PDF/A cannot be made")


def _inspect(data: bytes, level: Level) -> tuple[bool, bool, bool]:
    with pikepdf.open(io.BytesIO(data)) as pdf:
        declares = False
        with pdf.open_metadata() as meta:
            part, conformance = meta.get("pdfaid:part"), meta.get("pdfaid:conformance")
            declares = part == str(_PART[level]) and (conformance or "").upper() == level[1].upper()
        intents = pdf.Root.get("/OutputIntents")
        has_intent = intents is not None and len(intents) > 0
        embedded = True
        for page in pdf.pages:
            resources = page.get("/Resources")
            fonts = resources.get("/Font") if resources is not None else None
            for font in fonts.values() if fonts is not None else ():
                embedded &= _font_embedded(font)
    return declares, has_intent, embedded


def _font_embedded(font: pikepdf.Object) -> bool:
    if str(font.get("/Subtype")) == "/Type3":
        return True  # drawn from its own glyph procedures: nothing to embed
    if str(font.get("/Subtype")) == "/Type0":
        font = font["/DescendantFonts"][0]
    descriptor = font.get("/FontDescriptor")
    return descriptor is not None and any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))


def _validate(path: Path, level: Level) -> tuple[bool, str]:
    result = run_tool("verapdf", ["--flavour", level, "--format", "text", path], timeout=600)
    first = (result.stdout.strip().splitlines() or [""])[0]
    return first.upper().startswith("PASS"), first


def convert_to_pdfa(document: Document, *, level: Level = "2b", validate: bool = True) -> PdfAResult:
    """The document as PDF/A. Refused clearly (before any work) if Ghostscript is missing."""
    ghostscript = require_tool("ghostscript")
    profile = _srgb_profile(ghostscript)
    with tempfile.TemporaryDirectory(prefix="pdfworkerz-pdfa-") as folder:
        work = Path(folder)
        source, target, definitions = work / "in.pdf", work / "out.pdf", work / "PDFA_def.ps"
        source.write_bytes(document.to_bytes())
        definitions.write_text(
            _PDFA_DEF.format(profile=profile.as_posix().replace("(", r"\(").replace(")", r"\)")), encoding="ascii"
        )
        result = run_tool(
            "ghostscript",
            [
                f"-dPDFA={_PART[level]}",
                "-dBATCH", "-dNOPAUSE", "-dQUIET", "-dNOOUTERSAVE",
                "-sColorConversionStrategy=RGB", "-sProcessColorModel=DeviceRGB",
                "-dPDFACompatibilityPolicy=1",
                "-sDEVICE=pdfwrite",
                f"--permit-file-read={profile.as_posix()}",
                f"-sOutputFile={target}",
                definitions, source,
            ],
            timeout=600,
        )  # fmt: skip
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            detail = (result.stderr or result.stdout).strip()[:400] or "no message"
            raise ConversionError(f"Ghostscript could not make a PDF/A file: {detail}")
        data = target.read_bytes()
        declares, intent, embedded = _inspect(data, level)
        notes: list[str] = []
        valid: bool | None = None
        validator: str | None = None
        if validate and find_tool("verapdf"):
            valid, verdict = _validate(target, level)
            validator = "veraPDF"
            notes.append(f"veraPDF: {verdict}")
        elif validate:
            notes.append(
                "veraPDF is not installed, so this file was checked by PDFWorkerz but not independently validated"
            )
    return PdfAResult(data, level, declares, intent, embedded, validator, valid, notes)
