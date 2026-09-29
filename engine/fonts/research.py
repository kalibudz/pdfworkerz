"""Fonts to research: every font an edit could not match exactly is recorded here, so it can be
found and added to PDFWorkerz's own font library (assets/fonts, with its license) later.

The owner's rule (2026-09-28): prefer the exact font; when it isn't available, fall back to
the closest approximation, and flag that font for research. The list is a small JSON file in
the user's data folder -- never inside a document, and nothing leaves the machine. Set
PDFWORKERZ_DATA_DIR to put it somewhere else (the test suite does).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.fonts.classify import split_subset_tag

_FILE_NAME = "fonts_to_research.json"


@dataclass
class FontToResearch:
    font: str
    """The font's name as the PDF gives it (subset tag removed)."""
    best_tier: str
    """The best match PDFWorkerz found for it: "approximate" or "fallback"."""
    note: str
    """What was used instead, as the edit reported it."""
    times_seen: int
    first_seen: str
    last_seen: str
    example_document: str


def data_dir() -> Path:
    override = os.environ.get("PDFWORKERZ_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "pdfworkerz"


def research_file() -> Path:
    return data_dir() / _FILE_NAME


def load() -> list[FontToResearch]:
    path = research_file()
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [FontToResearch(**row) for row in rows]
    except (ValueError, TypeError):
        return []  # a damaged list is rebuilt from the next flags rather than blocking edits


def flag(base_font: str, *, tier: str, note: str, document: str) -> None:
    """Record that `base_font` could only be matched at `tier` (anything but "exact")."""
    name = split_subset_tag(base_font)[1] or base_font
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = load()
    for row in rows:
        if row.font == name:
            row.times_seen += 1
            row.last_seen = now
            if tier == "approximate" or row.best_tier != "approximate":
                row.best_tier, row.note = tier, note
            break
    else:
        rows.append(FontToResearch(name, tier, note, 1, now, now, document))
    path = research_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (-row.times_seen, row.font.lower()))
    path.write_text(json.dumps([asdict(row) for row in rows], indent=2) + "\n", encoding="utf-8")


def clear() -> None:
    research_file().unlink(missing_ok=True)
