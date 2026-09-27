"""PDFWorkerz engine: deterministic, offline PDF reading, inspection and editing.

No function in this package calls out to a network or an AI service. Every
capability here is backed by a real, version-pinned library call (see
pyproject.toml), verified against the installed version rather than assumed.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.1"
