"""Finding the external programs some features lean on (P7): Tesseract for OCR,
LibreOffice for Word/Excel/PowerPoint to PDF, Ghostscript for PDF/A and veraPDF to
validate it.

None of them ships with PDFWorkerz, and none is needed to edit a PDF. So each is looked
up on demand -- first the environment variable that names it, then ``PATH``, then the
folders its installer uses on Windows -- and a feature that needs one that isn't there
raises :class:`~engine.errors.MissingToolError` saying what to install, before it has
changed anything. :func:`tool_report` is what the UI reads to grey out what can't run.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from engine.errors import ConversionError, MissingToolError

_PROGRAM_FILES = (os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("PROGRAMFILES(X86)", ""))


@dataclass(frozen=True)
class ToolSpec:
    """How to find one external program, and what to tell the user when it's missing."""

    name: str
    label: str
    executables: tuple[str, ...]
    env_var: str
    windows_globs: tuple[str, ...]
    install_hint: str
    used_for: str


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "tesseract",
            "Tesseract OCR",
            ("tesseract",),
            "PDFWORKERZ_TESSERACT",
            (r"Tesseract-OCR\tesseract.exe",),
            "Install it with `winget install UB-Mannheim.TesseractOCR` (Windows), "
            "`sudo apt install tesseract-ocr` (Linux) or `brew install tesseract` (macOS).",
            "OCR, searchable PDFs, upright scans and editing scanned text",
        ),
        ToolSpec(
            "soffice",
            "LibreOffice",
            ("soffice", "libreoffice"),
            "PDFWORKERZ_SOFFICE",
            (r"LibreOffice\program\soffice.exe",),
            "Install it with `winget install TheDocumentFoundation.LibreOffice` (Windows), "
            "`sudo apt install libreoffice` (Linux) or `brew install --cask libreoffice` (macOS).",
            "Word, Excel and PowerPoint to PDF",
        ),
        ToolSpec(
            "ghostscript",
            "Ghostscript",
            ("gs", "gswin64c", "gswin32c"),
            "PDFWORKERZ_GHOSTSCRIPT",
            (r"gs\gs*\bin\gswin64c.exe", r"gs\gs*\bin\gswin32c.exe"),
            "Install it from https://ghostscript.com/releases/gsdnld.html (Windows), "
            "`sudo apt install ghostscript` (Linux) or `brew install ghostscript` (macOS).",
            "PDF to PDF/A",
        ),
        ToolSpec(
            "verapdf",
            "veraPDF",
            ("verapdf",),
            "PDFWORKERZ_VERAPDF",
            (r"veraPDF\verapdf.bat",),
            "Optional: install it from https://verapdf.org/software/ to have PDF/A results validated.",
            "independent PDF/A validation",
        ),
    )
}


@dataclass(frozen=True)
class ToolStatus:
    """One tool as the UI shows it."""

    name: str
    label: str
    available: bool
    path: str | None
    install_hint: str
    used_for: str


def find_tool(name: str) -> Path | None:
    """Where the program is installed, or None. The environment variable wins, so a
    user with an unusual install (or a test) can point at exactly the one to use."""
    spec = TOOLS[name]
    override = os.environ.get(spec.env_var)
    if override:
        path = Path(override)
        return path if path.is_file() else None
    for executable in spec.executables:
        found = shutil.which(executable)
        if found:
            return Path(found)
    for root in filter(None, _PROGRAM_FILES):
        for pattern in spec.windows_globs:
            matches = sorted(Path(root).glob(pattern), reverse=True)  # the newest version first
            if matches:
                return matches[0]
    return None


def require_tool(name: str) -> Path:
    """The program's path, or a :class:`MissingToolError` that says how to get it."""
    path = find_tool(name)
    if path is None:
        spec = TOOLS[name]
        raise MissingToolError(
            name, f"{spec.label} is not installed, and is needed for {spec.used_for}. {spec.install_hint}"
        )
    return path


def tool_report() -> list[ToolStatus]:
    """Every external tool and whether it is available -- what the UI asks before offering a feature."""
    report = []
    for spec in TOOLS.values():
        path = find_tool(spec.name)
        report.append(
            ToolStatus(
                spec.name, spec.label, path is not None, str(path) if path else None, spec.install_hint, spec.used_for
            )
        )
    return report


def run_tool(
    name: str, args: Sequence[str | Path], *, timeout: float = 300, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a tool to completion, capturing its output. A tool that hangs or can't start is a
    :class:`ConversionError` (with what it printed), never a raw subprocess exception."""
    command = [str(require_tool(name)), *map(str, args)]
    try:
        # The program is one find_tool located, never user text; the arguments are a list, so no shell is involved.
        return subprocess.run(  # nosec B603
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, **(env or {})},
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"{TOOLS[name].label} did not finish within {timeout:.0f} seconds") from exc
    except OSError as exc:
        raise ConversionError(f"{TOOLS[name].label} could not be started: {exc}") from exc
