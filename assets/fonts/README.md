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

Also: **Roboto** Light and Regular (`Roboto-Light.ttf`, `Roboto-Regular.ttf`),
SIL Open Font License 1.1 (`OFL-Roboto.txt`; the license statement is also
embedded in each font's own `name` table, IDs 13/14). These are the unmodified
`web/static/` files from the official
[Roboto v3.016 release](https://github.com/googlefonts/roboto-3-classic/releases/tag/v3.016)
(`Roboto_v3.016.zip`). The web build covers Latin-1, Latin Extended-A, Cyrillic
and most Greek in ~157KB each, against ~397KB for the full unhinted build.
Roboto is a very common document font (statements, invoices, anything produced
from Android or Google tooling). With these bundled, a document's Roboto text
resolves to the **exact** tier by name (FNT-06) instead of an approximate metric
match. The release zip ships no separate license file, so `OFL-Roboto.txt` is the
standard OFL-1.1 text with Roboto's copyright line.

Also: **Open Sans**, in ten static styles: Light, Regular, SemiBold, Bold and ExtraBold, each with its Italic (`OpenSans-*.ttf`). It is under the SIL Open Font License 1.1 (`OFL-OpenSans.txt`; the license statement is also in each font's `name` table, IDs 13/14), and every file allows installable embedding (fsType 0).
- These are the unmodified `fonts/ttf/` files, version 3.003, from the official [googlefonts/opensans](https://github.com/googlefonts/opensans) repository at commit `bd7e376`, and `OFL-OpenSans.txt` is that repository's `OFL.txt`.
- The Condensed styles are left out: documents rarely use them, and they would double the size.
- Open Sans is one of the most common document fonts (forms, letters, anything made with Google or Microsoft tools that default to it). With it bundled, a document's Open Sans text resolves to the **exact** tier by name (FNT-06), even for characters the document's own subset never held. It is also offered by name in the editor's font list.

**Follow-up** (tracked in PROGRESS.md): widen the set further — serif and
monospace (for example Noto Serif and Roboto Mono, both OFL-licensed), plus the
other Roboto weights and italics. Nothing in `engine.fonts.match` assumes any
particular font; adding more is just dropping more font files in.

## Not here: the user's own font library

Only openly licensed fonts belong in this folder: it is redistributed with the code.
A commercial font a document uses (a bank statement's Adobe "Delta Jaeger", a company's
licensed house font) goes in the **user's font library** instead
(`%LOCALAPPDATA%\pdfworkerz\fonts` on Windows; see `engine/fonts/library.py`). It stays
on that computer, never in the repository, and every edit searches it ahead of the
system's fonts. Add to it with `pdfworkerz fonts --add FILE`, with
`pdfworkerz fonts --harvest DOC.pdf FONTNAME` (the complete program a PDF embeds, when
its license flags permit embedding), or from the web UI's **Fonts** dialog.
