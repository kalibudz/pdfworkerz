"""P4: the command language (CMD-01..06), previews (CMD-05), batches and recipes (CMD-07)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.commands import CommandError, complete, parse_command
from engine.document import Document
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.preview import preview_ops
from engine.recipes import RecipeError, as_single_op, dump_recipe, load_recipe


def _doc(path: Path, pages: list[list[str]]) -> Path:
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + i * 20), line, fontsize=12, fontname="helv")
    doc.save(path)
    return path


def _texts(doc: Document, page: int = 0) -> list[str]:
    return [s.style.text for s in extract_page_spans(doc.raw, page)]


# -- CMD-01: the grammar produces the same Ops a click would --


@pytest.mark.feature("CMD-01")
@pytest.mark.parametrize(
    "command, expected",
    [
        (
            'replace "2024" with "2025" on all pages',
            {"op": "replace_text", "match": "2024", "mode": "literal", "replacement": "2025", "page_index": None},
        ),
        (
            'delete "DRAFT" on page 2',
            {"op": "delete_text", "match": "DRAFT", "mode": "literal", "page_index": 1},
        ),
        (
            'insert "Checked by" below "Prepared by"',
            {
                "op": "insert_text",
                "text": "Checked by",
                "anchor": "below",
                "reference_match": "Prepared by",
                "page_index": 0,
            },
        ),
        (
            'set size 11 bold for "Note:" on page 3',
            {"op": "restyle_text", "match": "Note:", "mode": "literal", "size": 11.0, "bold": True, "page_index": 2},
        ),
    ],
)
def test_commands_parse_into_ops(command: str, expected: dict[str, object]) -> None:
    plan = parse_command(command, page_count=5)
    assert plan.op() == expected
    parse_op(plan.op())  # and it is a valid, registered Op


@pytest.mark.feature("CMD-01")
def test_keywords_are_case_insensitive_and_quotes_can_be_escaped() -> None:
    plan = parse_command('REPLACE "say \\"hi\\"" WITH "bye" ON ALL PAGES', page_count=1)
    assert plan.op()["match"] == 'say "hi"'


@pytest.mark.feature("CMD-01")
def test_undo_and_redo_are_journal_actions() -> None:
    assert parse_command("undo", page_count=1).special == "undo"
    assert parse_command("Redo", page_count=1).special == "redo"


@pytest.mark.feature("CMD-01")
def test_a_command_over_several_pages_is_one_batch() -> None:
    plan = parse_command('replace "a" with "b" on pages 1-2', page_count=3)
    batch = plan.op()
    assert batch["op"] == "batch" and batch["command"] == 'replace "a" with "b" on pages 1-2'
    assert [op["page_index"] for op in batch["ops"]] == [0, 1]


# -- CMD-02: target selectors --


@pytest.mark.feature("CMD-02")
@pytest.mark.parametrize(
    "scope, pages",
    [
        ("on page 2", [1]),
        ("on pages 1-3,5", [0, 1, 2, 4]),
        ("on odd pages", [0, 2, 4]),
        ("on even pages", [1, 3]),
        ("on all pages", [None]),
        ("", [None]),
    ],
)
def test_page_selectors(scope: str, pages: list[int | None]) -> None:
    ops = parse_command(f'delete "x" {scope}', page_count=5).ops
    assert [op["page_index"] for op in ops] == pages


@pytest.mark.feature("CMD-02")
def test_regex_text_and_whole_word_targets() -> None:
    regex = parse_command(r'replace /Rev\s+[A-C]/i with "Rev D"', page_count=1).op()
    assert (regex["mode"], regex["match"], regex["case_sensitive"]) == ("regex", r"Rev\s+[A-C]", False)
    everything = parse_command("set size 10 for text", page_count=1).op()
    assert (everything["mode"], everything["match"]) == ("regex", ".+")
    word = parse_command('delete "cat" whole word', page_count=1).op()
    assert word["whole_word"] is True


@pytest.mark.feature("CMD-02")
def test_a_page_that_does_not_exist_is_refused() -> None:
    with pytest.raises(CommandError, match="page 9 doesn't exist: the document has 5 page"):
        parse_command('delete "x" on pages 2,9', page_count=5)


@pytest.mark.feature("CMD-02")
def test_an_invalid_regex_is_reported_not_run() -> None:
    with pytest.raises(CommandError, match="not valid"):
        parse_command("delete /([/ on page 1", page_count=1)


# -- CMD-03: style modifiers --


@pytest.mark.feature("CMD-03")
def test_style_modifiers() -> None:
    op = parse_command('set font "Times" italic not bold color #cc0000 size 9.5 for "x"', page_count=1).op()
    assert op["font"] == "Times" and op["italic"] is True and op["bold"] is False
    assert op["color"] == pytest.approx((0.8, 0.0, 0.0)) and op["size"] == 9.5
    named = parse_command('set color blue for "x"', page_count=1).op()
    assert named["color"] == pytest.approx((0.0, 0.2, 0.8))


@pytest.mark.feature("CMD-03")
def test_insert_matches_style_by_default_or_takes_an_explicit_one() -> None:
    matched = parse_command('insert "A" after "B" match style', page_count=1).op()
    assert "font" not in matched and matched["anchor"] == "after"
    explicit = parse_command('insert "A" at 72, 700 font "Courier" size 14 bold', page_count=1).op()
    assert (explicit["font"], explicit["size"], explicit["bold"], explicit["position"]) == (
        "Courier",
        14,
        True,
        (72, 700),
    )
    with pytest.raises(CommandError, match="needs a font and a size"):
        parse_command('insert "A" at 72, 700', page_count=1)


# -- CMD-06: never guesses; suggests instead --


@pytest.mark.feature("CMD-06")
def test_a_misspelled_command_is_refused_with_the_corrected_form_suggested() -> None:
    with pytest.raises(CommandError) as raised:
        parse_command('repalce "a" with "b"', page_count=1)
    assert "isn't a command" in str(raised.value)
    assert raised.value.suggestions[0] == 'replace "a" with "b"'
    assert raised.value.hint


@pytest.mark.feature("CMD-06")
def test_an_incomplete_command_explains_where_it_went_wrong() -> None:
    with pytest.raises(CommandError, match="near") as raised:
        parse_command('replace "a" "b"', page_count=1)
    assert any(s.startswith("replace") for s in raised.value.suggestions)


@pytest.mark.feature("CMD-06")
def test_actions_from_later_phases_say_when_they_arrive() -> None:
    with pytest.raises(CommandError, match="'watermark' isn't available yet: it is planned for P5"):
        parse_command('watermark "CONFIDENTIAL"', page_count=1)


# -- CMD-04: autocomplete (the engine side) --


@pytest.mark.feature("CMD-04")
@pytest.mark.parametrize(
    "typed, expected",
    [
        (
            "",
            {
                "replace",
                "delete",
                "insert",
                "set",
                "undo",
                "redo",
                "rotate",
                "move",
                "duplicate",
                "extract",
                "split",
                "merge",
                "remove",
                "crop",
                "uncrop",
                "resize",
                "impose",
            },
        ),
        ("rep", {"replace"}),
        ('replace "a" ', {"with"}),
        ('replace "a" with "b" on ', {"page", "pages", "all", "odd", "even"}),
        ("set bo", {"bold"}),
    ],
)
def test_completion_offers_what_can_come_next(typed: str, expected: set[str]) -> None:
    assert set(complete(typed)) == expected


@pytest.mark.feature("CMD-04")
def test_completion_offers_placeholders_for_values() -> None:
    assert {'"text"', "/regex/"} <= set(complete("replace "))


# -- CMD-05: preview counts without changing anything --


@pytest.mark.feature("CMD-05")
def test_preview_counts_matches_per_page_and_changes_nothing(tmp_path: Path) -> None:
    path = _doc(tmp_path / "p.pdf", [["Rev C drawing", "Rev C notes"], ["Nothing here"], ["Rev C"]])
    doc = Document.open(path)
    before = [_texts(doc, p) for p in range(3)]
    plan = parse_command('replace "Rev C" with "Rev D"', page_count=3)
    preview = preview_ops(doc, plan.ops)
    assert (preview.matches, preview.pages, preview.warnings) == (3, [1, 3], [])
    assert [_texts(doc, p) for p in range(3)] == before
    doc.close()


@pytest.mark.feature("CMD-05")
def test_preview_warns_when_nothing_would_change(tmp_path: Path) -> None:
    doc = Document.open(_doc(tmp_path / "p.pdf", [["Hello"]]))
    preview = preview_ops(doc, parse_command('delete "absent"', page_count=1).ops)
    assert preview.matches == 0 and "nothing matches" in preview.warnings[0]
    anchor = preview_ops(doc, parse_command('insert "x" below "absent"', page_count=1).ops)
    assert "wasn't found" in anchor.warnings[0]
    doc.close()


# -- applying: batches and anchored inserts --


@pytest.mark.feature("CMD-01")
def test_a_batch_is_one_undo_step(tmp_path: Path) -> None:
    doc = Document.open(_doc(tmp_path / "p.pdf", [["Rev C"], ["Rev C"]]))
    journal = UndoRedoJournal(doc)
    journal.record(parse_op(parse_command('replace "Rev C" with "Rev D" on pages 1-2', page_count=2).op()))
    assert _texts(journal.document, 0) == ["Rev D"] and _texts(journal.document, 1) == ["Rev D"]
    assert len(journal.history) == 1
    journal.undo()
    assert _texts(journal.document, 0) == ["Rev C"] and _texts(journal.document, 1) == ["Rev C"]
    journal.document.close()


@pytest.mark.feature("CMD-01")
def test_insert_below_places_the_text_one_line_under_its_reference(tmp_path: Path) -> None:
    doc = Document.open(_doc(tmp_path / "p.pdf", [["Prepared by: A.B."]]))
    op = parse_op(parse_command('insert "Checked by: K.H." below "Prepared by"', page_count=1).op())
    op.apply(doc)
    spans = {s.style.text: s.style for s in extract_page_spans(doc.raw, 0)}
    prepared, checked = spans["Prepared by: A.B."], spans["Checked by: K.H."]
    assert checked.chars[0].origin[0] == pytest.approx(prepared.chars[0].origin[0], abs=0.5)
    assert checked.chars[0].origin[1] == pytest.approx(prepared.chars[0].origin[1] + 12 * 1.2, abs=0.5)
    assert checked.font == prepared.font and checked.size == pytest.approx(prepared.size)
    doc.close()


# -- CMD-07: recipes --


@pytest.mark.feature("CMD-07")
def test_history_round_trips_through_a_recipe(tmp_path: Path) -> None:
    doc = Document.open(_doc(tmp_path / "p.pdf", [["Rev C", "Total due"]]))
    journal = UndoRedoJournal(doc)
    journal.record(parse_op(parse_command('replace "Rev C" with "Rev D"', page_count=1).op()))
    journal.record(parse_op(parse_command('set bold for "Total"', page_count=1).op()))
    for fmt in ("yaml", "json"):
        recipe = load_recipe(dump_recipe(journal.history, name="bump", fmt=fmt))
        assert recipe.name == "bump"
        assert [op["op"] for op in recipe.ops] == ["replace_text", "restyle_text"]
    journal.document.close()


@pytest.mark.feature("CMD-07")
def test_replaying_a_recipe_reproduces_the_edits_byte_for_byte(tmp_path: Path) -> None:
    source = _doc(tmp_path / "p.pdf", [["Rev C", "Total due"]])
    recipe = load_recipe(
        "recipe: bump\n"
        "ops:\n"
        "  - {op: replace_text, match: Rev C, replacement: Rev D}\n"
        "  - {op: restyle_text, match: Total, bold: true}\n"
    )
    outputs = []
    for run in (1, 2):
        doc = Document.open(source)
        parse_op(as_single_op(recipe)).apply(doc)
        out = tmp_path / f"run{run}.pdf"
        doc.save(out, deterministic=True)
        doc.close()
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]
    with pymupdf.open(tmp_path / "run1.pdf") as saved:
        assert saved[0].get_text().split("\n")[:2] == ["Rev D", "Total due"]


@pytest.mark.feature("CMD-07")
@pytest.mark.parametrize(
    "text, message",
    [
        ("ops: []", "has no ops"),
        ("just words", 'needs an "ops" list'),
        ("ops:\n  - {op: nonsense}", "step 1: unknown op 'nonsense'"),
        ("ops:\n  - {op: replace_text}", "step 1"),
        ("ops: [\n", "isn't valid YAML or JSON"),
    ],
)
def test_a_bad_recipe_is_refused_with_the_step_that_is_wrong(text: str, message: str) -> None:
    with pytest.raises(RecipeError, match=message):
        load_recipe(text)


@pytest.mark.feature("CMD-07")
def test_a_deterministic_save_of_an_encrypted_file_still_opens_with_its_password(tmp_path: Path) -> None:
    import shutil

    from tests.corpus.build_corpus import ENCRYPTED_TEXT, build_corpus

    corpus = build_corpus()
    source = tmp_path / "enc.pdf"
    shutil.copy(corpus.encrypted_aes_256, source)
    # Not byte-identical: AES encrypts every stream with a fresh random IV on each save, by
    # design. What must hold is that pinning the /ID leaves the file openable and intact.
    doc = Document.open(source, password=corpus.user_password)
    doc.save(tmp_path / "out1.pdf", deterministic=True)
    doc.close()
    with pymupdf.open(tmp_path / "out1.pdf") as reopened:
        assert reopened.needs_pass and reopened.authenticate(corpus.user_password)
        assert reopened[0].get_text().strip() == ENCRYPTED_TEXT


@pytest.mark.feature("CMD-07")
@pytest.mark.parametrize("second_id", [b"<6CFFB31AA18E59C856FBD967BDB550AD>", rb"(\345O\364$9y\)D9(D)\324)"])
def test_the_random_second_file_id_is_pinned_in_either_string_form(tmp_path: Path, second_id: bytes) -> None:
    from engine.document import _pin_second_file_id

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "x")
    data = doc.tobytes(no_new_id=True)
    head, tail = data.rsplit(b"trailer", 1)
    tail = tail.replace(b"/Root 1 0 R", b"/Root 1 0 R/ID[<1301C2B73AC38543C3A6C3A7C3BF15C2>" + second_id + b"]", 1)
    path = tmp_path / "id.pdf"
    path.write_bytes(head + b"trailer" + tail)
    _pin_second_file_id(path)
    once = path.read_bytes()
    _pin_second_file_id(path)
    assert path.read_bytes() == once  # stable
    assert second_id not in once and b"/ID[<1301C2B73AC38543C3A6C3A7C3BF15C2><" in once
    with pymupdf.open(path) as reopened:
        assert reopened[0].get_text().strip() == "x"


# -- P4 review findings (2026-09-29) --


@pytest.mark.feature("CMD-01")
def test_set_whole_word_reaches_the_op_and_leaves_longer_words_alone(tmp_path: Path) -> None:
    doc = Document.open(_doc(tmp_path / "p.pdf", [["cat", "category"]]))
    plan = parse_command('set bold for "cat" whole word', page_count=1)
    assert plan.op()["whole_word"] is True and "whole words only" in plan.description
    assert preview_ops(doc, plan.ops).matches == 1
    parse_op(plan.op()).apply(doc)
    fonts = {s.style.text: s.style.font for s in extract_page_spans(doc.raw, 0)}
    assert fonts == {"cat": "Helvetica-Bold", "category": "Helvetica"}
    doc.close()


@pytest.mark.feature("CMD-06")
@pytest.mark.parametrize(
    "data, message",
    [
        ({"op": "replace_text", "match": "", "replacement": "y"}, "empty"),
        ({"op": "delete_text", "match": "x*", "mode": "regex"}, "matches empty text"),
        ({"op": "restyle_text", "match": "(", "mode": "regex", "bold": True}, "not valid"),
    ],
)
def test_searches_that_would_match_everywhere_or_nothing_sensible_are_refused(
    data: dict[str, object], message: str
) -> None:
    from engine.errors import OpValidationError

    with pytest.raises(OpValidationError, match=message):
        parse_op(data)


@pytest.mark.feature("CMD-06")
def test_an_empty_search_is_refused_by_the_parser_and_in_a_recipe() -> None:
    with pytest.raises(CommandError, match="empty"):
        parse_command('replace "" with "y"', page_count=1)
    with pytest.raises(RecipeError, match=r"step 1: .*empty"):
        load_recipe('ops:\n  - {op: replace_text, match: "", replacement: y}')


@pytest.mark.feature("CMD-05")
def test_a_dry_run_counts_each_step_after_the_earlier_ones(tmp_path: Path) -> None:
    from engine.preview import preview_steps

    doc = Document.open(_doc(tmp_path / "p.pdf", [["Hello one", "Hello two"]]))
    recipe = load_recipe(
        "ops:\n"
        "  - {op: replace_text, match: Hello, replacement: Howdy}\n"
        "  - {op: restyle_text, match: Howdy, bold: true}\n"
    )
    preview = preview_steps(doc, recipe.ops)
    assert [part.matches for part in preview.ops] == [2, 2]
    assert preview.warnings == []
    assert _texts(doc) == ["Hello one", "Hello two"]  # the real document is untouched
    doc.close()


@pytest.mark.feature("CMD-05")
def test_a_dry_run_reports_a_step_that_would_fail(tmp_path: Path) -> None:
    from engine.preview import preview_steps

    doc = Document.open(_doc(tmp_path / "p.pdf", [["Hello"]]))
    preview = preview_steps(doc, [{"op": "replace_span_text", "page_index": 0, "span_index": 7, "new_text": "x"}])
    assert "step 1 would fail" in preview.warnings[0]
    doc.close()


@pytest.mark.feature("CMD-01")
def test_quoted_strings_resolve_both_escapes() -> None:
    backslash, quote = chr(92), chr(34)
    command = (
        f"replace {quote}a{backslash}{backslash}b {backslash}{quote}x{backslash}{quote}{quote} with {quote}c{quote}"
    )
    assert parse_command(command, page_count=1).op()["match"] == f"a{backslash}b {quote}x{quote}"


@pytest.mark.feature("CMD-05")
def test_descriptions_name_every_option_and_repeats_are_refused() -> None:
    plan = parse_command('delete "x" whole word ignoring case', page_count=1)
    assert plan.description.endswith("whole words only, ignoring case")
    assert "in its style with bold" in parse_command('insert "A" below "B" bold', page_count=1).description
    with pytest.raises(CommandError, match="given twice"):
        parse_command('set size 5 size 9 for "x"', page_count=1)
    with pytest.raises(CommandError, match="out of range"):
        parse_command('set size 0.5 for "x"', page_count=1)
    with pytest.raises(CommandError, match="doesn't apply"):
        parse_command('delete "x" fit', page_count=1)


@pytest.mark.feature("CMD-02")
@pytest.mark.parametrize("current_page", [-1, 3])
def test_the_current_page_must_exist(current_page: int) -> None:
    with pytest.raises(CommandError, match="doesn't exist"):
        parse_command('insert "x" below "y"', page_count=3, current_page=current_page)


@pytest.mark.feature("CMD-07")
def test_a_deterministic_save_that_cannot_replace_the_file_leaves_it_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine.errors import SaveFailedError

    source = _doc(tmp_path / "p.pdf", [["Rev C"]])
    original = source.read_bytes()
    doc = Document.open(source)
    parse_op({"op": "replace_text", "match": "Rev C", "replacement": "Rev D"}).apply(doc)

    def locked(self: Path, target: Path) -> Path:
        raise PermissionError(13, "in use")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", locked)
        with pytest.raises(SaveFailedError):
            doc.save(overwrite=True, deterministic=True)
    assert source.read_bytes() == original
    assert not list(tmp_path.glob(".*pdfworkerz-tmp"))
    doc.close()


@pytest.mark.feature("CMD-06")
@pytest.mark.parametrize("pattern", [chr(92) + "b", "(?=a)", "(?<=l)"])
def test_a_zero_width_pattern_is_refused_on_real_text(tmp_path: Path, pattern: str) -> None:
    from engine.errors import OpValidationError

    doc = Document.open(_doc(tmp_path / "p.pdf", [["Hello cat"]]))
    op = parse_op({"op": "replace_text", "match": pattern, "mode": "regex", "replacement": "Q"})
    with pytest.raises(OpValidationError, match="empty stretch"):
        op.apply(doc)
    with pytest.raises(OpValidationError, match="empty stretch"):
        preview_ops(doc, [op.model_dump()])
    assert _texts(doc) == ["Hello cat"]
    doc.close()


@pytest.mark.feature("CMD-07")
def test_a_dry_run_step_on_a_missing_page_is_a_warning_not_a_crash(tmp_path: Path) -> None:
    from engine.preview import preview_steps

    doc = Document.open(_doc(tmp_path / "p.pdf", [["Hello"]]))
    preview = preview_steps(doc, [{"op": "replace_text", "match": "Hello", "replacement": "x", "page_index": 99}])
    assert "step 1 would fail" in preview.warnings[0] and "out of range" in preview.warnings[0]
    doc.close()


@pytest.mark.feature("CMD-07")
def test_a_deterministic_incremental_save_says_it_is_not_reproducible(corpus: object, tmp_path: Path) -> None:
    import shutil

    from tests.corpus.build_corpus import build_corpus

    source = tmp_path / "signed.pdf"
    shutil.copy(build_corpus().form_and_signature, source)
    doc = Document.open(source)
    result = doc.save(overwrite=True, deterministic=True)
    assert result.mode == "incremental" and "not byte-for-byte reproducible" in result.note
    doc.close()


# -- P5 slice 1: page commands --


@pytest.mark.feature("ORG-03")
@pytest.mark.parametrize(
    "command, expected",
    [
        ("delete pages 7, 9-10", {"op": "delete_pages", "page_indices": [6, 8, 9]}),
        ("move pages 5-6 after page 1", {"op": "move_pages", "page_indices": [4, 5], "to": 1}),
        ("move page 2 before page 1", {"op": "move_pages", "page_indices": [1], "to": 0}),
        ("move page 1 to the end", {"op": "move_pages", "page_indices": [0], "to": 9}),
        ("rotate pages 2-3", {"op": "rotate_pages", "page_indices": [1, 2], "degrees": 90}),
        ("rotate page 1 by 90 counterclockwise", {"op": "rotate_pages", "page_indices": [0], "degrees": 270}),
        ("rotate odd pages by 180", {"op": "rotate_pages", "page_indices": [0, 2, 4, 6, 8], "degrees": 180}),
        ("duplicate page 1 2 times", {"op": "duplicate_pages", "page_indices": [0], "copies": 2}),
        ("insert 2 blank pages before page 3", {"op": "insert_pages", "at": 2, "count": 2}),
        ('extract pages 1-2 to "a.pdf"', {"op": "extract_pages", "page_indices": [0, 1], "out": "a.pdf"}),
        ('split every 4 pages into "parts"', {"op": "split", "out_dir": "parts", "every": 4}),
        ('split at bookmarks into "parts"', {"op": "split", "out_dir": "parts", "by_bookmarks": True}),
        ('merge "b.pdf" after page 4', {"op": "merge", "path": "b.pdf", "at": 4}),
        ("remove blank pages", {"op": "remove_blank_pages"}),
    ],
)
def test_page_commands_parse_into_page_ops(command: str, expected: dict[str, object]) -> None:
    plan = parse_command(command, page_count=10)
    assert plan.op() == expected
    parse_op(plan.op())


@pytest.mark.feature("ORG-05")
@pytest.mark.parametrize(
    "command, message",
    [
        ("delete all pages", "at least one"),
        ("rotate page 1 by 45", "multiples of 90"),
        ("move page 2 after page 2", "one of the pages being moved"),
        ("move page 1 below page 3", 'use "after" or "before"'),
        ("delete pages 3-12", "page 11 doesn't exist"),
        ("watermark page 1", "isn't available yet"),
    ],
)
def test_page_commands_refuse_what_cannot_be_done(command: str, message: str) -> None:
    with pytest.raises(CommandError, match=message):
        parse_command(command, page_count=10)


@pytest.mark.feature("ORG-03")
def test_a_page_command_applies_through_the_journal(tmp_path: Path) -> None:
    doc = Document.open(_doc(tmp_path / "p.pdf", [["One"], ["Two"], ["Three"]]))
    journal = UndoRedoJournal(doc)
    journal.record(parse_op(parse_command("move page 3 to the start", page_count=3).op()))
    assert [journal.document.raw[i].get_text().strip() for i in range(3)] == ["Three", "One", "Two"]
    journal.undo()
    assert journal.document.raw[0].get_text().strip() == "One"
    journal.document.close()
