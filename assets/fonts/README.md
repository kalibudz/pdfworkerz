# Bundled fallback fonts

These are the fonts `engine.fonts.match` (FNT-06) searches when a document's
own font can't be reused and no matching font is installed on the system.

Currently: the **Bitstream Vera** family (`Vera.ttf`, `VeraBd.ttf`,
`VeraIt.ttf`, `VeraBI.ttf`), under the license in `bitstream-vera-license.txt`
— free to embed and redistribute. It's a placeholder starting set (sourced
from `reportlab`'s own bundled copy, which ships it for the same reason),
chosen because it is genuinely open, small, and works identically on every
platform this project targets, rather than depending on whatever happens to
be installed locally.

**Follow-up** (tracked in PROGRESS.md): widen this to a proper small OFL
family set with real Unicode-range breadth (Latin, Cyrillic, Greek; serif,
sans and monospace) — for example Google Fonts' Open Sans / Noto Serif /
Roboto Mono, all OFL-licensed. Nothing in `engine.fonts.match` assumes Vera
specifically; adding more fonts here is just dropping more `.ttf` files in.
