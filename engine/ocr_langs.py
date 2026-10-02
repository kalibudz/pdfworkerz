"""OCR-02: which Tesseract language packs are available, choosing among them, and adding more.

Tesseract reads one folder of ``<code>.traineddata`` files. The packs that came with the
install live in the system's folder, which PDFWorkerz never writes to (it may need admin
rights, and belongs to another program). Packs the user adds go in their own folder under
the PDFWorkerz data directory; a language is found in either, the user's folder first.
When a run needs packs from both folders, the few files it needs are gathered into one
cached folder, because Tesseract can only be pointed at one.
"""

from __future__ import annotations

import re
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from engine.errors import ConversionError, OcrError
from engine.external import require_tool, run_tool
from engine.fonts.research import data_dir

DOWNLOAD_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{code}.traineddata"
MAX_PACK_BYTES = 250 * 1024 * 1024
_CODE = re.compile(r"^[A-Za-z]{3}(_[A-Za-z0-9]+)*$")
_HEADER_DIR = re.compile(r'in "(.+?)/?" \(\d+\)')

# The packs people most often add; any other code is shown as itself.
LANGUAGE_NAMES = {
    "eng": "English", "deu": "German", "fra": "French", "spa": "Spanish", "ita": "Italian",
    "por": "Portuguese", "nld": "Dutch", "swe": "Swedish", "nor": "Norwegian", "dan": "Danish",
    "fin": "Finnish", "pol": "Polish", "ces": "Czech", "hun": "Hungarian", "ron": "Romanian",
    "tur": "Turkish", "ell": "Greek", "rus": "Russian", "ukr": "Ukrainian", "bul": "Bulgarian",
    "heb": "Hebrew", "ara": "Arabic", "fas": "Persian", "hin": "Hindi", "ben": "Bengali",
    "tha": "Thai", "vie": "Vietnamese", "ind": "Indonesian", "chi_sim": "Chinese (Simplified)",
    "chi_tra": "Chinese (Traditional)", "jpn": "Japanese", "kor": "Korean", "lat": "Latin",
    "osd": "Orientation and script detection",
}  # fmt: skip
_NOT_A_LANGUAGE = {"osd"}  # present in every install, but it only detects orientation


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    installed_by: str
    """"Tesseract" for a pack that came with the install, "you" for one added in PDFWorkerz."""


def user_tessdata() -> Path:
    """The folder for packs the user added."""
    return data_dir() / "tessdata"


def system_tessdata() -> Path:
    """The folder Tesseract itself reads, as it reports it."""
    out = run_tool("tesseract", ["--list-langs"], timeout=30).stdout
    match = _HEADER_DIR.search(out)
    if not match:
        raise OcrError("Tesseract did not say where its language packs are; its output was unexpected")
    return Path(match.group(1))


def _packs_in(folder: Path) -> dict[str, Path]:
    return {p.stem: p for p in folder.glob("*.traineddata")} if folder.is_dir() else {}


def available_packs() -> dict[str, Path]:
    """Every pack Tesseract could use, by code. The user's own wins over the system's."""
    return {**_packs_in(system_tessdata()), **_packs_in(user_tessdata())}


def installed_languages() -> list[Language]:
    """The languages OCR can read, sorted by name."""
    user = _packs_in(user_tessdata())
    rows = [
        Language(code, LANGUAGE_NAMES.get(code, code), "you" if code in user else "Tesseract")
        for code in available_packs()
        if code not in _NOT_A_LANGUAGE
    ]
    return sorted(rows, key=lambda row: row.name.lower())


def parse_language(spec: str) -> list[str]:
    """``"eng+deu"`` -> ``["eng", "deu"]``, refusing anything that isn't a pack code before it
    gets near a command line."""
    codes = [part for part in spec.strip().split("+") if part]
    if not codes or not all(_CODE.match(code) for code in codes):
        raise OcrError(f"{spec!r} is not a language code; use one like eng, or several joined with +, like eng+deu")
    return codes


def resolve_languages(spec: str) -> tuple[str, Path]:
    """Check a language choice against what is installed and return the ``-l`` value together
    with the one folder that holds every pack it needs. An unknown language is refused here,
    before any OCR work, listing what is installed and how to add the rest."""
    codes = parse_language(spec)
    packs = available_packs()
    missing = [code for code in codes if code not in packs]
    if missing:
        have = ", ".join(language.code for language in installed_languages()) or "none"
        raise OcrError(
            f"The language pack {', '.join(missing)} is not installed. Installed: {have}. "
            "Add one from the OCR dialog, or with `pdfworkerz ocr-languages --install CODE`."
        )
    folders = {packs[code].parent for code in codes}
    if len(folders) == 1:
        return "+".join(codes), folders.pop()
    gathered = data_dir() / "tessdata-cache"
    gathered.mkdir(parents=True, exist_ok=True)
    for code in codes:
        target = gathered / f"{code}.traineddata"
        if not target.exists() or target.stat().st_size != packs[code].stat().st_size:
            shutil.copyfile(packs[code], target)
    return "+".join(codes), gathered


def install_language(code: str, source: Path | bytes | None = None) -> Language:
    """Add a language pack to the user's folder: from ``source`` (a file or its bytes, for an
    offline machine), or downloaded from the Tesseract project's own pack repository. Only
    runs when asked for -- OCR itself never touches the network."""
    require_tool("tesseract")
    if not _CODE.match(code):
        raise OcrError(f"{code!r} is not a language code")
    if isinstance(source, Path):
        data = source.read_bytes()
    elif source is not None:
        data = source
    else:
        data = _download(DOWNLOAD_URL.format(code=code))
    if len(data) < 1024:
        raise OcrError(f"That file is too small to be a {code} language pack")
    target = user_tessdata() / f"{code}.traineddata"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    partial.write_bytes(data)
    partial.replace(target)  # a half-written pack is never left where Tesseract would find it
    return Language(code, LANGUAGE_NAMES.get(code, code), "you")


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310
            data: bytes = response.read(MAX_PACK_BYTES + 1)
    except OSError as exc:
        raise ConversionError(
            f"The language pack could not be downloaded ({exc}). Check the code and your connection"
        ) from exc
    if len(data) > MAX_PACK_BYTES:
        raise ConversionError("The language pack is larger than expected; refusing it")
    return data
