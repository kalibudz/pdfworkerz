"""EDT-16..18 in the browser: the Block | Line | Word selection mode.

Same wiring as test_ui.py (a real server, the built frontend, a real Chromium;
see conftest.py). The grouping itself is the server's job and is proven in
tests/engine; these tests prove the UI on top of it: the toggle, what the
inspector shows for a unit, and that editing and arranging a line or a word is
one Op and one undo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

_EDITOR = "#pw-edit-text"
_SELECTED = ".pw-span-box.pw-span-selected"
_MODE_KEY = "pdfworkerz.selectMode"
_PAGE_INDICATOR_READY = "() => /^\\d+ \\/ \\d+$/.test(document.querySelector('.pw-page-indicator')?.textContent ?? '')"

LINE_ONE = "Alpha beta gamma"
LINE_TWO = "Delta epsilon"
LINE_THREE = "Zeta eta"
MIXED_LINE = "Plain Bold"


@pytest.fixture
def units_pdf(tmp_path: Path) -> Path:
    """A three-line paragraph (seven words), and further down one line made of two
    style runs, regular then bold: 4 lines and 9 words, in fewer blocks than lines."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), LINE_ONE, fontname="helv", fontsize=12)
    page.insert_text((72, 114.4), LINE_TWO, fontname="helv", fontsize=12)
    page.insert_text((72, 128.8), LINE_THREE, fontname="helv", fontsize=12)
    page.insert_text((72, 200), "Plain ", fontname="helv", fontsize=12)
    start = 72 + pymupdf.get_text_length("Plain ", fontname="helv", fontsize=12)
    page.insert_text((start, 200), "Bold", fontname="hebo", fontsize=12)
    path = tmp_path / "units.pdf"
    doc.save(path)
    return path


def _open_document(page: Page, path: Path) -> None:
    page.wait_for_selector("#pw-open-path", timeout=5000)
    page.fill("#pw-open-path", str(path))
    page.click("button[type=submit]")
    page.wait_for_selector(".pw-viewer", timeout=5000)
    page.wait_for_function(_PAGE_INDICATOR_READY, timeout=5000)
    page.wait_for_selector(".pw-span-box", timeout=10000)


def _open(page: Page, app_url: str, path: Path, mode: str | None = None) -> None:
    """Open `path`; with `mode`, as a returning user whose last choice was that mode."""
    if mode is not None:
        page.add_init_script(f"window.localStorage.setItem('{_MODE_KEY}', '{mode}')")
    page.goto(app_url)
    _open_document(page, path)
    if mode is not None:
        page.wait_for_selector(f".pw-span-box.pw-unit-{mode}", timeout=10000)


def _mode(page: Page) -> str:
    chosen = page.eval_on_selector_all(
        ".pw-select-mode button[aria-checked='true']", "els => els.map(e => e.dataset.mode)"
    )
    assert len(chosen) == 1, chosen  # a radio group: exactly one
    return str(chosen[0])


def _wait_mode(page: Page, mode: str) -> None:
    """The mode is chosen and the page's boxes have been redrawn for it."""
    page.wait_for_selector(f".pw-select-mode button[data-mode='{mode}'][aria-checked='true']", timeout=5000)
    page.wait_for_function(
        "m => { const boxes = [...document.querySelectorAll('.pw-span-box')];"
        " return boxes.length > 0 && boxes.every(b => b.classList.contains('pw-unit-' + m)); }",
        arg=mode,
        timeout=10000,
    )


def _texts(page: Page) -> list[str]:
    return list(page.eval_on_selector_all(".pw-span-box", "els => els.map(e => e.textContent)"))


def _box(page: Page, text: str) -> dict[str, float]:
    """The layer-pixel rectangle of the text box whose text is exactly `text`."""
    rect = page.evaluate(
        "t => { const b = [...document.querySelectorAll('.pw-span-box')].find(x => x.textContent === t);"
        " return b ? { left: parseFloat(b.style.left), top: parseFloat(b.style.top),"
        " width: parseFloat(b.style.width) } : null; }",
        text,
    )
    assert rect is not None, f"no text box {text!r} among {_texts(page)}"
    return dict(rect)


def _click_text(page: Page, text: str, *, shift: bool = False) -> None:
    index = _texts(page).index(text)
    page.locator(".pw-span-box").nth(index).click(modifiers=["Shift"] if shift else [])


def _select_for_keys(page: Page, text: str) -> None:
    """Click a unit, then hand the keyboard back to the page (Esc in the unchanged editor)."""
    _click_text(page, text)
    page.wait_for_selector(_SELECTED, timeout=3000)
    page.wait_for_function(f"() => document.activeElement?.id === '{_EDITOR[1:]}'", timeout=3000)
    page.keyboard.press("Escape")
    page.wait_for_function(f"() => document.activeElement?.id !== '{_EDITOR[1:]}'", timeout=3000)


def _history(page: Page) -> list[str]:
    return [str(t) for t in page.eval_on_selector_all(".pw-history-entry", "els => els.map(e => e.textContent)")]


def _wait_history(page: Page, count: int, text: str = "") -> None:
    page.wait_for_function(
        "([n, t]) => { const e = [...document.querySelectorAll('.pw-history-entry')];"
        " return e.length === n && (!t || e[e.length - 1].textContent.includes(t)); }",
        arg=[count, text],
        timeout=10000,
    )


def _undo(page: Page, remaining: int) -> None:
    """One undo, and the history is back to `remaining` entries: the action was one Op."""
    page.click(".pw-history button:has-text('Undo')")
    _wait_history(page, remaining)


def _scale(page: Page) -> float:
    """Layer pixels per page point (the fixture page is 595pt wide)."""
    return float(page.evaluate("() => parseFloat(document.querySelector('.pw-edit-layer').style.width) / 595"))


def _wait_box(page: Page, text: str, field: str, expected: float, tolerance: float = 2.0) -> None:
    page.wait_for_function(
        "([t, f, e, tol]) => [...document.querySelectorAll('.pw-span-box')]"
        ".some(b => b.textContent === t && Math.abs(parseFloat(b.style[f]) - e) <= tol)",
        arg=[text, field, expected, tolerance],
        timeout=10000,
    )


# -- EDT-16: the toggle, and what the inspector shows --


@pytest.mark.feature("EDT-16", criterion=4)
def test_toolbar_toggles_block_line_word_and_redraws_the_boxes(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    group = page.locator(".pw-select-mode")
    assert group.get_attribute("role") == "radiogroup"
    assert page.eval_on_selector_all(".pw-select-mode button", "els => els.map(e => e.dataset.mode)") == [
        "block",
        "line",
        "word",
    ]
    # Placed before the Align menu.
    assert page.evaluate(
        "() => !!(document.querySelector('.pw-select-mode').compareDocumentPosition("
        "document.querySelector('.pw-align-select')) & Node.DOCUMENT_POSITION_FOLLOWING)"
    )

    # Line is the default: one box per line, whatever its style runs.
    assert _mode(page) == "line"
    _wait_mode(page, "line")
    assert _texts(page) == [LINE_ONE, LINE_TWO, LINE_THREE, MIXED_LINE]

    page.click(".pw-select-mode button[data-mode='word']")
    _wait_mode(page, "word")
    assert _texts(page) == ["Alpha", "beta", "gamma", "Delta", "epsilon", "Zeta", "eta", "Plain", "Bold"]
    assert page.get_attribute(".pw-select-mode button[data-mode='word']", "role") == "radio"
    assert page.get_attribute(".pw-select-mode button[data-mode='line']", "aria-checked") == "false"
    assert page.get_attribute(".pw-select-mode button[data-mode='word']", "aria-pressed") is None  # radios are checked

    page.click(".pw-select-mode button[data-mode='block']")
    _wait_mode(page, "block")
    blocks = _texts(page)
    # EDT-19: the paragraph is one block, and the two-run line is one block, not two.
    assert blocks == [f"{LINE_ONE} {LINE_TWO} {LINE_THREE}", MIXED_LINE]
    # Every box keeps the shared class and says which unit and span it is.
    assert page.eval_on_selector_all(
        ".pw-span-box", "els => els.every(e => e.dataset.unitIndex !== undefined && e.dataset.spanIndex !== undefined)"
    )


@pytest.mark.feature("EDT-16", criterion=4)
def test_b_l_w_keys_switch_mode_but_not_while_typing(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    page.keyboard.press("w")
    _wait_mode(page, "word")
    page.keyboard.press("b")
    _wait_mode(page, "block")
    page.keyboard.press("l")
    _wait_mode(page, "line")
    page.keyboard.press("Control+b")  # a modifier: not the shortcut
    page.wait_for_timeout(200)
    assert _mode(page) == "line"

    # A focused menu takes its letter keys for itself.
    page.focus(".pw-draw-select")
    page.keyboard.press("w")
    page.keyboard.press("b")
    page.wait_for_timeout(200)
    assert _mode(page) == "line"
    page.focus(".pw-page-area")

    # The shortcuts are listed, and typing the letters into the editor is just typing.
    page.keyboard.press("?")
    page.wait_for_selector(".pw-shortcuts[open]", timeout=5000)
    assert "B / L / W" in (page.text_content(".pw-shortcuts") or "")
    page.keyboard.press("Escape")
    _click_text(page, LINE_TWO)
    page.wait_for_function(f"() => document.activeElement?.id === '{_EDITOR[1:]}'", timeout=3000)
    page.keyboard.type("bwl")
    assert page.input_value(_EDITOR) == "bwl"
    assert _mode(page) == "line"


@pytest.mark.feature("EDT-16", criterion=4)
def test_changing_mode_clears_the_selection(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    _select_for_keys(page, LINE_ONE)
    assert page.locator(".pw-arrange-outline").count() == 1
    page.keyboard.press("w")
    _wait_mode(page, "word")
    assert page.query_selector(_SELECTED) is None
    assert page.locator(".pw-arrange-outline").count() == 0
    assert page.is_hidden(_EDITOR)
    assert page.is_disabled(".pw-align-select")


@pytest.mark.feature("EDT-16", criterion=4)
def test_the_chosen_mode_is_remembered_across_a_reload(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    assert page.evaluate(f"() => window.localStorage.getItem('{_MODE_KEY}')") is None  # never chosen: Line
    page.click(".pw-select-mode button[data-mode='word']")
    _wait_mode(page, "word")
    assert page.evaluate(f"() => window.localStorage.getItem('{_MODE_KEY}')") == "word"

    page.reload()
    _open_document(page, units_pdf)
    _wait_mode(page, "word")
    assert len(_texts(page)) == 9


@pytest.mark.feature("EDT-16", criterion=5)
def test_inspector_shows_the_units_text_and_marks_mixed_style_fields(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    _click_text(page, MIXED_LINE)
    page.wait_for_selector(_SELECTED, timeout=3000)
    assert page.text_content(".pw-unit-kind") == "Line"
    assert page.input_value(_EDITOR) == MIXED_LINE  # the whole line, both style runs
    rows = page.eval_on_selector_all(
        ".pw-inspector-row", "els => els.map(el => el.querySelector('.pw-inspector-value').textContent.trim())"
    )
    font, size, color = rows[0], rows[1], rows[2]
    assert font == "(mixed)"  # Helvetica and Helvetica-Bold
    assert size == "12.0 pt" and color == "#000000"  # the same in both runs
    assert page.eval_on_selector("#pw-edit-bold", "el => el.indeterminate") is True
    assert page.eval_on_selector("#pw-edit-italic", "el => el.indeterminate") is False
    assert "(mixed)" in (page.text_content("#pw-edit-font option[value='']") or "")
    assert page.is_disabled("#pw-edit-apply")  # showing "(mixed)" is not a change

    # A line in one style has nothing mixed.
    _click_text(page, LINE_ONE)
    page.wait_for_function(f"() => document.querySelector('{_EDITOR}')?.value === '{LINE_ONE}'", timeout=3000)
    assert page.eval_on_selector("#pw-edit-bold", "el => el.indeterminate") is False
    assert "Helvetica" in (page.text_content("#pw-edit-font option[value='']") or "")

    # Word mode: one word of the mixed line is a single style again.
    page.keyboard.press("Escape")
    page.keyboard.press("w")
    _wait_mode(page, "word")
    _click_text(page, "Bold")
    page.wait_for_selector(_SELECTED, timeout=3000)
    assert page.text_content(".pw-unit-kind") == "Word"
    assert page.input_value(_EDITOR) == "Bold"
    assert page.is_checked("#pw-edit-bold") is True
    assert page.eval_on_selector("#pw-edit-bold", "el => el.indeterminate") is False
    assert page.is_disabled(".pw-copy-style")  # the format painter is span-level: off in Word mode

    page.keyboard.press("Escape")
    page.keyboard.press("b")
    _wait_mode(page, "block")
    page.locator(".pw-span-box").first.click()
    page.wait_for_selector(_SELECTED, timeout=3000)
    assert page.text_content(".pw-unit-kind") == "Block"
    assert LINE_TWO in page.input_value(_EDITOR)
    assert page.locator(".pw-resize-handle").count() == 1  # blocks keep the resize handle


# -- EDT-17: editing one word or line --


@pytest.mark.feature("EDT-17", criterion=5)
def test_editing_a_word_is_one_undo_and_leaves_the_rest_of_the_line(page: Page, app_url: str, units_pdf: Path) -> None:
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    _open(page, app_url, units_pdf, mode="word")
    alpha = _box(page, "Alpha")
    delta = _box(page, "Delta")

    _click_text(page, "beta")
    page.wait_for_selector(_SELECTED, timeout=3000)
    assert page.locator(".pw-move-handle").count() == 1
    assert page.locator(".pw-resize-handle").count() == 0  # a word only moves
    page.fill(_EDITOR, "BETA")
    page.keyboard.press("Enter")
    _wait_history(page, 1, "BETA")
    assert dialogs == []

    # The edited word is selected again, ready for the next change.
    page.wait_for_function(
        f"() => document.querySelector('{_SELECTED}')?.textContent === 'BETA'"
        f" && document.querySelector('{_EDITOR}')?.value === 'BETA'",
        timeout=10000,
    )
    assert page.text_content(".pw-unit-kind") == "Word"
    # Re-extracted from the server: only that word changed; its neighbours did not move.
    assert _texts(page) == ["Alpha", "BETA", "gamma", "Delta", "epsilon", "Zeta", "eta", "Plain", "Bold"]
    assert _box(page, "Alpha") == pytest.approx(alpha, abs=0.5)
    assert _box(page, "Delta") == pytest.approx(delta, abs=0.5)
    page.keyboard.press("Escape")  # unchanged editor: the keyboard goes back to the page
    page.keyboard.press("l")
    _wait_mode(page, "line")
    assert _texts(page) == ["Alpha BETA gamma", LINE_TWO, LINE_THREE, MIXED_LINE]

    _undo(page, 0)
    page.wait_for_function(
        "t => [...document.querySelectorAll('.pw-span-box')].some(b => b.textContent === t)",
        arg=LINE_ONE,
        timeout=10000,
    )
    assert _texts(page) == [LINE_ONE, LINE_TWO, LINE_THREE, MIXED_LINE]


@pytest.mark.feature("EDT-17", criterion=5)
def test_editing_a_line_commits_once_and_reselects_it(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    _click_text(page, LINE_TWO)
    page.wait_for_selector(_SELECTED, timeout=3000)
    page.fill(_EDITOR, "Delta zeta")
    page.keyboard.press("Enter")
    _wait_history(page, 1, "Delta zeta")
    page.wait_for_function(f"() => document.querySelector('{_SELECTED}')?.textContent === 'Delta zeta'", timeout=10000)
    assert page.input_value(_EDITOR) == "Delta zeta"
    assert _texts(page) == [LINE_ONE, "Delta zeta", LINE_THREE, MIXED_LINE]  # the other lines are untouched
    _undo(page, 0)


# -- EDT-18: arranging lines and words --


@pytest.mark.feature("EDT-18", criterion=6)
def test_dragging_a_line_moves_only_that_line_as_one_undo(page: Page, app_url: str, units_pdf: Path) -> None:
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    _open(page, app_url, units_pdf)
    before = _box(page, LINE_THREE)
    line_one = _box(page, LINE_ONE)
    line_two = _box(page, LINE_TWO)

    _click_text(page, LINE_THREE)
    page.wait_for_selector(".pw-move-handle", timeout=3000)
    assert page.locator(".pw-resize-handle").count() == 0
    handle = page.locator(".pw-move-handle").bounding_box()
    assert handle is not None
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 20, y + 20, steps=4)
    page.keyboard.down("Alt")  # no snapping: the move is exactly the drag
    page.mouse.move(x + 40, y + 50)  # into the empty space under the paragraph
    page.mouse.up()
    page.keyboard.up("Alt")

    _wait_history(page, 1, "Move 1 object")
    _wait_box(page, LINE_THREE, "top", before["top"] + 50)
    assert _box(page, LINE_THREE)["left"] == pytest.approx(before["left"] + 40, abs=2)
    # The rest of its block stayed where it was.
    assert _box(page, LINE_ONE) == pytest.approx(line_one, abs=0.5)
    assert _box(page, LINE_TWO) == pytest.approx(line_two, abs=0.5)
    assert page.locator(".pw-arrange-outline").count() == 1  # still selected where it landed
    assert dialogs == []

    _undo(page, 0)
    _wait_box(page, LINE_THREE, "top", before["top"], tolerance=0.5)


@pytest.mark.feature("EDT-18", criterion=6)
def test_nudging_and_aligning_a_line(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    scale = _scale(page)
    before = _box(page, MIXED_LINE)

    _select_for_keys(page, MIXED_LINE)
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Shift+ArrowRight")
    _wait_history(page, 1, "Move 1 object")  # presses in quick succession are one move
    _wait_box(page, MIXED_LINE, "left", before["left"] + 12 * scale)
    assert (page.text_content(".pw-page-indicator") or "").startswith("1 /")  # nudged, not paged
    assert page.locator(".pw-arrange-outline").count() == 1

    # One object aligns to the page: its left margin (36pt).
    assert page.is_enabled(".pw-align-select")
    page.select_option(".pw-align-select", "left")
    _wait_history(page, 2, "Move 1 object")
    _wait_box(page, MIXED_LINE, "left", 36 * scale)

    _undo(page, 1)
    _wait_box(page, MIXED_LINE, "left", before["left"] + 12 * scale)
    _undo(page, 0)
    _wait_box(page, MIXED_LINE, "left", before["left"], tolerance=0.5)


@pytest.mark.feature("EDT-18", criterion=6)
def test_words_shift_click_marquee_and_align(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf, mode="word")
    bold = _box(page, "Bold")
    gamma = _box(page, "gamma")
    _click_text(page, "Bold")
    _click_text(page, "gamma", shift=True)
    assert page.locator(".pw-arrange-outline").count() == 2
    # Bottoms line up on the lower one: "gamma" drops to the end of the mixed line, clear of its words.
    page.select_option(".pw-align-select", "bottom")
    _wait_history(page, 1, "Move 1 object")  # "Bold" is already the lowest
    _wait_box(page, "gamma", "top", bold["top"])
    assert _box(page, "gamma")["left"] == pytest.approx(gamma["left"], abs=1)
    assert _box(page, "Alpha")["top"] == pytest.approx(gamma["top"], abs=1)  # its old line stayed put
    assert page.locator(".pw-arrange-outline").count() == 2  # both still selected
    _undo(page, 0)
    _wait_box(page, "gamma", "top", gamma["top"], tolerance=0.5)

    # A marquee around the mixed line's two words selects exactly those.
    plain = page.locator(".pw-span-box").nth(_texts(page).index("Plain")).bounding_box()
    bold = page.locator(".pw-span-box").nth(_texts(page).index("Bold")).bounding_box()
    assert plain is not None and bold is not None
    page.mouse.move(plain["x"] - 15, plain["y"] - 15)
    page.mouse.down()
    page.mouse.move(plain["x"] + 30, plain["y"] + 5)
    page.mouse.move(bold["x"] + bold["width"] + 15, bold["y"] + bold["height"] + 15)
    page.mouse.up()
    assert page.locator(".pw-arrange-outline").count() == 2
    before = _box(page, "Bold")
    page.keyboard.press("ArrowDown")
    _wait_history(page, 1, "Move 2 objects")
    _wait_box(page, "Bold", "top", before["top"] + _scale(page))
    _undo(page, 0)


@pytest.mark.feature("EDT-18", criterion=6)
def test_duplicating_copying_and_pasting_a_line(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    count = "t => [...document.querySelectorAll('.pw-span-box')].filter(b => b.textContent === t).length"
    _select_for_keys(page, LINE_THREE)
    page.keyboard.press("Control+d")
    _wait_history(page, 1, "Copy 1 object")
    page.wait_for_function(f"t => ({count})(t) === 2", arg=LINE_THREE, timeout=10000)
    assert page.locator(".pw-arrange-outline").count() == 1  # the copy is the selection now

    page.keyboard.press("Control+c")
    page.keyboard.press("Control+v")
    _wait_history(page, 2, "Copy 1 object")
    page.wait_for_function(f"t => ({count})(t) === 3", arg=LINE_THREE, timeout=10000)
    assert page.evaluate(count, LINE_ONE) == 1  # only that line was copied, not its paragraph

    _undo(page, 1)
    page.wait_for_function(f"t => ({count})(t) === 2", arg=LINE_THREE, timeout=10000)
    _undo(page, 0)
    page.wait_for_function(f"t => ({count})(t) === 1", arg=LINE_THREE, timeout=10000)


@pytest.mark.feature("EDT-18", criterion=6)
def test_deleting_a_word_is_one_undo(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf, mode="word")
    _select_for_keys(page, "gamma")
    page.keyboard.press("Delete")
    _wait_history(page, 1, "Delete 1 object")
    page.wait_for_function("() => document.querySelectorAll('.pw-span-box').length === 8", timeout=10000)
    assert _texts(page) == ["Alpha", "beta", "Delta", "epsilon", "Zeta", "eta", "Plain", "Bold"]
    page.keyboard.press("l")
    _wait_mode(page, "line")
    assert _texts(page) == ["Alpha beta", LINE_TWO, LINE_THREE, MIXED_LINE]  # the word and one space went

    _undo(page, 0)
    page.wait_for_function(
        "t => [...document.querySelectorAll('.pw-span-box')].some(b => b.textContent === t)",
        arg=LINE_ONE,
        timeout=10000,
    )


@pytest.mark.feature("EDT-17", criterion=5)
def test_a_word_edited_into_two_words_reselects_the_first(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf, mode="word")
    _click_text(page, "beta")
    page.wait_for_selector(_SELECTED, timeout=3000)
    page.fill(_EDITOR, "BE TA")
    page.keyboard.press("Enter")
    _wait_history(page, 1, "BE TA")
    # Two words now; the one at the edit's origin is selected, not a fragment elsewhere.
    page.wait_for_function(
        f"() => document.querySelector('{_SELECTED}')?.textContent === 'BE'"
        f" && document.querySelector('{_EDITOR}')?.value === 'BE'",
        timeout=10000,
    )
    assert _texts(page)[:4] == ["Alpha", "BE", "TA", "gamma"]
    _undo(page, 0)


@pytest.mark.feature("EDT-18", criterion=6)
def test_pasting_text_that_changed_since_it_was_copied_says_so(page: Page, app_url: str, units_pdf: Path) -> None:
    messages: list[str] = []
    page.on("dialog", lambda dialog: (messages.append(dialog.message), dialog.accept()))
    _open(page, app_url, units_pdf)
    _select_for_keys(page, LINE_THREE)
    page.keyboard.press("Control+c")
    _click_text(page, LINE_THREE)
    page.fill(_EDITOR, "Zeta eta!")
    page.keyboard.press("Enter")
    _wait_history(page, 1, "Zeta eta!")
    page.wait_for_function(f"() => document.querySelector('{_EDITOR}')?.value === 'Zeta eta!'", timeout=10000)
    page.keyboard.press("Escape")  # unchanged editor: the keyboard goes back to the page
    page.keyboard.press("Control+v")
    for _ in range(50):
        if messages:
            break
        page.wait_for_timeout(100)
    assert messages == ["The copied text has changed since you copied it. Select it and copy it again."]
    assert len(_history(page)) == 1  # nothing was pasted


@pytest.mark.feature("EDT-18", criterion=6)
def test_a_word_drags_duplicates_copies_and_deletes(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf, mode="word")
    count = "t => [...document.querySelectorAll('.pw-span-box')].filter(b => b.textContent === t).length"
    before = _box(page, "Bold")
    plain = _box(page, "Plain")

    _click_text(page, "Bold")
    page.wait_for_selector(".pw-move-handle", timeout=3000)
    handle = page.locator(".pw-move-handle").bounding_box()
    assert handle is not None
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.keyboard.down("Alt")  # no snapping
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 25, steps=4)
    page.mouse.move(x, y + 50)
    page.mouse.up()
    page.keyboard.up("Alt")
    _wait_history(page, 1, "Move 1 object")
    _wait_box(page, "Bold", "top", before["top"] + 50)
    assert _box(page, "Plain") == pytest.approx(plain, abs=0.5)  # the rest of its line stayed

    page.focus(".pw-page-area")  # the keyboard to the page, the moved word still selected
    page.keyboard.press("Control+d")
    _wait_history(page, 2, "Copy 1 object")
    page.wait_for_function(f"t => ({count})(t) === 2", arg="Bold", timeout=10000)
    page.keyboard.press("Control+c")
    page.keyboard.press("Control+v")
    _wait_history(page, 3, "Copy 1 object")
    page.wait_for_function(f"t => ({count})(t) === 3", arg="Bold", timeout=10000)
    page.keyboard.press("Delete")  # the pasted copy is the selection
    _wait_history(page, 4, "Delete 1 object")
    page.wait_for_function(f"t => ({count})(t) === 2", arg="Bold", timeout=10000)
    _undo(page, 3)
    page.wait_for_function(f"t => ({count})(t) === 3", arg="Bold", timeout=10000)


@pytest.mark.feature("EDT-18", criterion=6)
def test_deleting_a_line_is_one_undo(page: Page, app_url: str, units_pdf: Path) -> None:
    _open(page, app_url, units_pdf)
    _select_for_keys(page, MIXED_LINE)
    page.keyboard.press("Delete")
    _wait_history(page, 1, "Delete 1 object")
    page.wait_for_function("() => document.querySelectorAll('.pw-span-box').length === 3", timeout=10000)
    assert _texts(page) == [LINE_ONE, LINE_TWO, LINE_THREE]
    _undo(page, 0)
    page.wait_for_function("() => document.querySelectorAll('.pw-span-box').length === 4", timeout=10000)
