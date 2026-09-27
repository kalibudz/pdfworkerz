"""Regenerate assets/fonts/NotoSansSC-Subset.otf (FNT-14's bundled CJK font).

    python tools/gen_noto_subset.py --source /path/to/NotoSansCJKsc-Regular.otf
    python tools/gen_noto_subset.py --source ... --text "additional characters"

The source font (~16MB) is Google's Noto Sans CJK
(https://github.com/notofonts/noto-cjk, SIL Open Font License) and is not
committed to this repo; download it yourself and pass its path with
--source. This script cuts it down, with fontTools' own subsetter, to just
the characters PDFWorkerz's tests actually exercise -- the same technique
engine.fonts.merge.build_merged_subset uses at runtime to cover an edit,
applied once here to keep the bundled file small (kilobytes, not
megabytes) while remaining genuinely reproducible.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "assets" / "fonts" / "NotoSansSC-Subset.otf"

# Covers tests/corpus/build_corpus.py's CJK fixture and tests/engine/test_font_cjk.py:
# common test vocabulary (hello/world/test/Chinese/font/edit/replace/insert/delete/
# style/color/size/page/document/new/text), full-width punctuation, and basic
# Latin/digits (Noto Sans CJK covers those too, so one font suffices for mixed text).
DEFAULT_TEXT = (
    "你好世界测试中文字体编辑替换插入删除样式颜色大小页面文档新文本"
    "，。！？：；“”（）—"  # noqa: RUF001 -- deliberate full-width CJK punctuation, not ASCII look-alikes
    "PDFWorkerzEditor0123456789 "
)


def build_subset(source_path: Path, text: str) -> bytes:
    tt = TTFont(source_path)
    options = subset.Options()
    options.glyph_names = True
    options.notdef_glyph = True
    options.notdef_outline = True
    options.layout_features = ["*"]
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(tt)

    from io import BytesIO

    buffer = BytesIO()
    tt.save(buffer)
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path, help="path to the full Noto Sans CJK SC font")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="characters to keep (default: the built-in test set)")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        parser.error(f"source font not found: {args.source}")

    data = build_subset(args.source, args.text)
    OUTPUT_PATH.write_bytes(data)
    print(f"{OUTPUT_PATH} written ({len(data):,} bytes, {len(set(args.text))} distinct characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
