"""Font and text intelligence: style extraction, classification, and matching.

See SPEC.md section 5 for the design this package implements: extract every
span's real rendering style (engine.fonts.style), classify the font behind
it (engine.fonts.classify), and -- from FNT-05 onward -- decide how to draw
replacement text in that same style (engine.fonts.match).
"""

from __future__ import annotations
