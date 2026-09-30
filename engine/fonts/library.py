"""The user's own font library: fonts added on this machine, searched by every edit
exactly like the bundled set (engine.fonts.match.build_font_index).

It exists for fonts that can't ship with PDFWorkerz. The bundled set in assets/fonts is
redistributed with the code, so it holds only openly licensed fonts. A commercial font a
document uses (a bank's Delta Jaeger, a company's licensed house font) belongs here
instead: in the user's data folder (engine.fonts.research.data_dir()), never in the
repository. A font gets in two ways:

- ``add_font_file``: a font file the user has.
- ``harvest_from_document``: the complete font program a PDF embeds. Only a complete,
  embeddable program qualifies. A subset holds just the characters that one document
  uses, so it would fail the very edits the library is for.

Every font is saved under its PostScript name, with a small JSON note beside it saying
where it came from and what its copyright notice says. Adding a font takes it off the
fonts-to-research list, and the next edit sees it (the process-wide font index is reset).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fontTools.ttLib import TTFont

from engine.errors import OpValidationError
from engine.fonts import research
from engine.fonts.classify import split_subset_tag
from engine.fonts.match import normalize_font_name, user_fonts_dir
from engine.fonts.resolve import FSTYPE_RESTRICTED, embedded_program_covers

if TYPE_CHECKING:
    from engine.document import Document

_COMPLETE_SAMPLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,;:!?-()$%&/'\""
"""What "complete" means for a harvested font: it can draw every basic Latin letter,
digit and common punctuation mark, each with a real outline."""


class FontLibraryError(OpValidationError):
    """A font that can't be added, with the reason in plain words."""


@dataclass
class LibraryFont:
    postscript_name: str
    family: str
    style: str
    file: str
    """The file's name inside the library folder."""
    source: str
    """Where it came from: the original file's path, or "embedded in <document>"."""
    added: str
    copyright: str


def _describe(tt: TTFont) -> tuple[str, str, str, str]:
    names = tt["name"]
    postscript = names.getDebugName(6) or ""
    family = names.getDebugName(16) or names.getDebugName(1) or ""
    style = names.getDebugName(17) or names.getDebugName(2) or ""
    copyright_notice = names.getDebugName(0) or ""
    return postscript, family, style, copyright_notice


def _check_embeddable(tt: TTFont, what: str) -> None:
    os2 = tt.get("OS/2", None)
    if os2 is not None and os2.fsType & FSTYPE_RESTRICTED:
        raise FontLibraryError(f"{what} is marked 'restricted license' by its vendor: it may not be embedded in PDFs")


def _save(program: bytes, extension: str, *, source: str) -> LibraryFont:
    tt = TTFont(io.BytesIO(program), lazy=True, fontNumber=0)
    postscript, family, style, copyright_notice = _describe(tt)
    if not postscript:
        raise FontLibraryError("the font has no PostScript name, so edits could never find it by name")
    _check_embeddable(tt, postscript)
    folder = user_fonts_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", postscript)
    target = folder / f"{stem}{extension}"
    target.write_bytes(program)
    entry = LibraryFont(
        postscript_name=postscript,
        family=family,
        style=style,
        file=target.name,
        source=source,
        added=datetime.now(UTC).isoformat(timespec="seconds"),
        copyright=copyright_notice,
    )
    target.with_suffix(".json").write_text(json.dumps(asdict(entry), indent=2) + "\n", encoding="utf-8")
    _after_change(postscript)
    return entry


def _after_change(postscript_name: str) -> None:
    from engine.ops.text import reset_font_index  # engine.ops imports engine.fonts, not the reverse

    reset_font_index()
    research.resolve(postscript_name)


def add_font_file(path: Path) -> LibraryFont:
    """Copy a TrueType/OpenType font file into the library."""
    if not path.is_file():
        raise FontLibraryError(f"{path} does not exist or is not a file")
    if path.suffix.lower() not in (".ttf", ".otf"):
        raise FontLibraryError(f"{path.name} is not a .ttf or .otf font file")
    program = path.read_bytes()
    try:
        TTFont(io.BytesIO(program), lazy=True, fontNumber=0)["name"]
    except Exception as exc:
        raise FontLibraryError(f"{path.name} could not be read as a font: {exc}") from exc
    return _save(program, path.suffix.lower(), source=str(path))


def _embedded_program(document: Document, base_font: str) -> tuple[str, bytes, str]:
    """(the PDF's name for it, its font program, its file extension) for the first
    embedded font named `base_font` anywhere in the document."""
    target = normalize_font_name(split_subset_tag(base_font)[1] or base_font)
    for page_index in range(document.raw.page_count):
        for xref, _extension, font_type, name, *_ in document.raw.get_page_fonts(page_index, full=False):
            if normalize_font_name(split_subset_tag(name)[1] or name) != target:
                continue
            if font_type == "Type3":
                raise FontLibraryError(f"{name} is a Type3 font: its glyphs are drawing procedures, not a font file")
            if split_subset_tag(name)[0]:
                raise FontLibraryError(
                    f"{name} is embedded as a subset (only the characters this document uses), not the whole font"
                )
            _, found_extension, _, program = document.raw.extract_font(xref)
            if not program:
                raise FontLibraryError(f"{name} is only named by this document, not embedded in it")
            if found_extension not in ("ttf", "otf"):
                raise FontLibraryError(f"{name} is embedded as a {found_extension} program, which edits can't reuse")
            return name, program, f".{found_extension}"
    raise FontLibraryError(f"this document has no font named {base_font!r}")


def harvestable(document: Document, base_font: str) -> str | None:
    """None if `base_font` can be harvested from `document`, otherwise why not."""
    try:
        name, program, _ = _embedded_program(document, base_font)
        _check_embeddable(TTFont(io.BytesIO(program), lazy=True, fontNumber=0), name)
    except FontLibraryError as exc:
        return str(exc)
    except Exception as exc:
        return f"{base_font}'s embedded program could not be read: {exc}"
    if not embedded_program_covers(program, _COMPLETE_SAMPLE):
        return f"{base_font} is embedded, but not completely: some basic letters or digits are missing or blank"
    return None


def harvest_from_document(document: Document, base_font: str) -> LibraryFont:
    """Copy the complete font program `document` embeds for `base_font` into the library."""
    problem = harvestable(document, base_font)
    if problem is not None:
        raise FontLibraryError(problem)
    name, program, extension = _embedded_program(document, base_font)
    source = document.source_path.name if document.source_path else "an unsaved document"
    entry = _save(program, extension, source=f"embedded in {source}")
    if normalize_font_name(name) != normalize_font_name(entry.postscript_name):
        research.resolve(name)  # listed under the PDF's name for it
    return entry


def list_fonts() -> list[LibraryFont]:
    fonts = []
    for note in sorted(user_fonts_dir().glob("*.json")):
        try:
            fonts.append(LibraryFont(**json.loads(note.read_text(encoding="utf-8"))))
        except (ValueError, TypeError):
            continue  # a damaged note hides that entry here; the font file itself still works
    return fonts


def remove_font(postscript_name: str) -> None:
    for entry in list_fonts():
        if entry.postscript_name == postscript_name:
            (user_fonts_dir() / entry.file).unlink(missing_ok=True)
            (user_fonts_dir() / entry.file).with_suffix(".json").unlink(missing_ok=True)
            from engine.ops.text import reset_font_index

            reset_font_index()
            return
    raise FontLibraryError(f"no font named {postscript_name!r} in the library")


__all__ = [
    "FontLibraryError",
    "LibraryFont",
    "add_font_file",
    "harvest_from_document",
    "harvestable",
    "list_fonts",
    "remove_font",
    "user_fonts_dir",
]
