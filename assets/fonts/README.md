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

Also: `NotoSansSC-Subset.otf`, for CJK (FNT-14) — a genuine, real, OFL-licensed
font (SIL Open Font License, `OFL-NotoSansCJK.txt`), cut down from Google's
[Noto Sans CJK](https://github.com/notofonts/noto-cjk) (16MB) with fontTools'
own subsetter (see `tools/gen_noto_subset.py`) to the ~65 characters
`tests/corpus/build_corpus.py`'s CJK fixture and its tests actually use — 29KB
instead of 16MB, and it still round-trips correctly through
`engine.fonts.merge.build_merged_subset` to draw *new* CJK text a document
never had, exactly like the Vera family does for Latin text. To widen its
coverage, regenerate it with `tools/gen_noto_subset.py --text "<more characters>"`.

**Follow-up** (tracked in PROGRESS.md): widen the Latin-script set to a proper
small OFL family with real Unicode-range breadth (Latin, Cyrillic, Greek;
serif, sans and monospace) — for example Google Fonts' Open Sans / Noto Serif
/ Roboto Mono, all OFL-licensed. Nothing in `engine.fonts.match` assumes Vera
specifically; adding more fonts here is just dropping more font files in.
