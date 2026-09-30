"""UI-01 (page canvas with thumbnail rail) and UI-06 (password prompt),
proven against a real, built frontend in a real browser -- not a DOM/unit
test double. See conftest.py for how the API server, the static build and
the browser are wired together.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.corpus.build_corpus import Corpus

_PASSWORD_VISIBLE = "() => !document.querySelector('#pw-open-password')?.closest('.pw-field')?.hidden"
_PAGE_INDICATOR_READY = "() => /^\\d+ \\/ \\d+$/.test(document.querySelector('.pw-page-indicator')?.textContent ?? '')"


def _wait_viewer_ready(page: Page) -> None:
    """`.pw-viewer` exists the instant the screen switches, before the
    document has actually loaded into pdf.js -- its controls are disabled
    until then (viewer.ts), so any test that clicks or presses a key must
    wait for the page indicator to show a real "n / total" first, not just
    for the viewer container to exist."""
    page.wait_for_selector(".pw-viewer", timeout=5000)
    page.wait_for_function(_PAGE_INDICATOR_READY, timeout=5000)


def _wait_overlay_ready(page: Page) -> None:
    """UI-02/UI-03: `_wait_viewer_ready` alone isn't enough -- the page
    indicator updates before this page's spans are fetched and the
    overlay's hit-boxes exist (viewer.ts fetches spans and builds them
    *after* rendering, not before). Confirmed by a real race caught this
    way during manual testing, not a hypothetical one."""
    _wait_viewer_ready(page)
    # 10s, not 5: under a full gate run the spans request once took just over 5s (load, not a race).
    page.wait_for_selector(".pw-span-box", timeout=10000)


def _open_path(page: Page, path: str, password: str | None = None) -> None:
    """Fills the path and submits. When `password` is given, that first
    submit is expected to come back 401 (no password sent yet) -- which is
    exactly what reveals the password field -- so this waits for it, fills
    it in, and submits a second time, rather than trying to pre-fill a
    field that can't be visible until a real round trip has failed once."""
    page.fill("#pw-open-path", path)
    page.click("button[type=submit]")
    if password is not None:
        page.wait_for_function(_PASSWORD_VISIBLE, timeout=5000)
        page.fill("#pw-open-password", password)
        page.click("button[type=submit]")


@pytest.mark.feature("UI-01")
def test_connect_screen_shown_without_session_config(page: Page, connect_only_url: str) -> None:
    page.goto(connect_only_url)
    page.wait_for_selector("#pw-connect-api", timeout=5000)


@pytest.mark.feature("UI-01")
def test_url_config_is_consumed_and_scrubbed_from_the_address_bar(page: Page, app_url: str) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    assert "token=" not in page.url
    assert "api=" not in page.url


@pytest.mark.feature("UI-01")
def test_missing_file_shows_the_engines_own_error_message(page: Page, app_url: str) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, "/no/such/file.pdf")
    page.wait_for_selector(".pw-error:not(:empty)", timeout=5000)
    assert "does not exist" in (page.text_content(".pw-error") or "")


@pytest.mark.feature("UI-06")
def test_encrypted_document_without_a_password_prompts_for_one(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.encrypted_aes_256))
    page.wait_for_function(_PASSWORD_VISIBLE, timeout=5000)
    assert "password protected" in (page.text_content(".pw-error") or "")


@pytest.mark.feature("UI-06")
def test_wrong_password_is_reported_as_incorrect_not_missing(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.encrypted_aes_256), password="definitely-wrong")
    page.wait_for_function(
        "() => document.querySelector('.pw-error')?.textContent?.includes('Incorrect')", timeout=5000
    )


@pytest.mark.feature("UI-06")
def test_correct_password_opens_the_viewer(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.encrypted_aes_256), password=corpus.user_password)
    _wait_viewer_ready(page)


@pytest.mark.feature("UI-01")
def test_viewer_shows_the_right_page_count_and_one_thumbnail_per_page(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.multi_page))
    _wait_viewer_ready(page)
    page.wait_for_function(
        f"() => document.querySelector('.pw-page-indicator')?.textContent?.trim() === '1 / {corpus.multi_page_count}'",
        timeout=5000,
    )
    assert page.eval_on_selector_all(".pw-thumb", "els => els.length") == corpus.multi_page_count


@pytest.mark.feature("UI-01")
def test_next_and_prev_buttons_change_page(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.multi_page))
    _wait_viewer_ready(page)

    page.click(".pw-toolbar button:has-text('Next')")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim().startsWith('2 /')", timeout=5000
    )
    page.click(".pw-toolbar button:has-text('Prev')")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim().startsWith('1 /')", timeout=5000
    )


@pytest.mark.feature("UI-01")
def test_arrow_keys_navigate_pages(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.multi_page))
    _wait_viewer_ready(page)

    page.keyboard.press("ArrowRight")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim().startsWith('2 /')", timeout=5000
    )
    page.keyboard.press("ArrowLeft")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim().startsWith('1 /')", timeout=5000
    )


@pytest.mark.feature("UI-01")
def test_clicking_a_thumbnail_jumps_to_that_page(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.multi_page))
    _wait_viewer_ready(page)

    page.click(".pw-thumb:nth-child(3)")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim().startsWith('3 /')", timeout=5000
    )
    assert page.eval_on_selector_all(".pw-thumb.pw-active", "els => els.length") == 1


@pytest.mark.feature("UI-01")
def test_zoom_in_increases_the_rendered_canvas_size(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_viewer_ready(page)

    before = page.eval_on_selector(".pw-page-area canvas", "el => el.width")
    page.click(".pw-toolbar button:has-text('+')")
    page.wait_for_function(f"() => document.querySelector('.pw-page-area canvas')?.width > {before}", timeout=5000)


# -- UI-02 (click-to-edit overlay) and UI-03 (inspector panel) --

_EXACT_DOT = "() => document.querySelector('.pw-match-dot')?.classList.contains('pw-match-exact')"
_FALLBACK_DOT = "() => document.querySelector('.pw-match-dot')?.classList.contains('pw-match-fallback')"
_SELECTED = ".pw-span-box.pw-span-selected"
_EDITOR = "#pw-edit-text"


def _editor_value(page: Page) -> str:
    return str(page.input_value(_EDITOR))


def _history_count(page: Page) -> int:
    return len(page.query_selector_all(".pw-history-entry"))


def _open_and_click_first_span(page: Page, app_url: str, path: str) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, path)
    _wait_overlay_ready(page)
    page.click(".pw-span-box")
    page.wait_for_selector(_SELECTED, timeout=3000)
    page.wait_for_function(f"() => document.activeElement?.id === '{_EDITOR[1:]}'", timeout=3000)


@pytest.mark.feature("UI-02")
def test_span_boxes_sit_over_their_text_not_mirrored(page: Page, app_url: str, corpus: Corpus) -> None:
    """MuPDF's span bboxes are y-down from the page's top-left; pdf.js
    converts from y-up PDF space. Mixing them once put every click target
    at the vertically mirrored position (top-of-page text clickable only
    near the bottom). simple.pdf's only line sits ~61-75pt down an 842pt
    page, so its box must be near the canvas top, in the same proportion."""
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_overlay_ready(page)
    top_fraction = page.evaluate(
        "() => parseFloat(document.querySelector('.pw-span-box').style.top)"
        " / document.querySelector('.pw-canvas-wrap canvas').getBoundingClientRect().height"
    )
    assert top_fraction == pytest.approx(61 / 842, abs=0.01)
    # And the visible text itself is what a click at its position hits.
    box = page.locator(".pw-span-box").bounding_box()
    assert box is not None
    hit = page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y)?.className ?? ''",
        [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
    )
    assert "pw-span-box" in hit


@pytest.mark.feature("UI-02")
def test_idle_span_boxes_do_not_draw_a_second_copy_of_the_text(page: Page, app_url: str, corpus: Corpus) -> None:
    """The canvas already shows the real glyphs. An idle box that painted
    its text too, in an approximated web font a few pixels off, doubled
    every word on a real bank statement. A box's text stays invisible,
    selected or not: the text is edited in the inspector, not on the page."""
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_overlay_ready(page)
    color = page.eval_on_selector(".pw-span-box", "el => getComputedStyle(el).color")
    assert color == "rgba(0, 0, 0, 0)"
    page.click(".pw-span-box")
    page.wait_for_selector(_SELECTED, timeout=3000)
    assert page.eval_on_selector(_SELECTED, "el => getComputedStyle(el).color") == "rgba(0, 0, 0, 0)"


@pytest.mark.feature("UI-02")
def test_clicking_a_span_puts_its_text_in_the_inspector_editor(page: Page, app_url: str, corpus: Corpus) -> None:
    """The owner's rule (2026-09-29): clicking text selects it and puts the
    cursor in the inspector's text box, prefilled with the text and its style;
    the page itself is never edited in place."""
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    assert _editor_value(page) == "Hello, PDFWorkerz."
    assert page.eval_on_selector(_SELECTED, "el => el.isContentEditable") is False
    assert page.input_value("#pw-edit-font") == ""  # the original font, by default
    assert page.input_value("#pw-edit-size") == "14"
    assert page.input_value("#pw-edit-color") == "#000000"
    assert page.is_checked("#pw-edit-bold") is False
    font_label = page.eval_on_selector("#pw-edit-font option[value='']", "el => el.textContent")
    assert "Original font" in font_label and "Helvetica" in font_label
    assert page.is_disabled("#pw-edit-apply")  # nothing changed yet


@pytest.mark.feature("UI-03")
def test_inspector_shows_the_selected_spans_style(page: Page, app_url: str, corpus: Corpus) -> None:
    """corpus.simple draws "Hello, PDFWorkerz." at 14pt with PyMuPDF's
    default color (black) and no explicit font -- fixed, known values to
    assert against rather than just "is non-empty"."""
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    rows = page.eval_on_selector_all(
        ".pw-inspector-row", "els => els.map(el => el.querySelector('.pw-inspector-value').textContent.trim())"
    )
    font, size, color, spacing, rotation, _match = rows
    assert size == "14.0 pt"
    assert color == "#000000"
    assert "helvetica" in font.lower()  # PyMuPDF's default standard font, per build_corpus.py's _simple()
    assert "Tc" in spacing
    assert rotation == "0°"


@pytest.mark.feature("UI-03")
def test_inspector_match_field_populates_as_soon_as_a_span_is_selected(
    page: Page, app_url: str, corpus: Corpus
) -> None:
    """Not just once the user starts typing -- SPEC.md's own mockup shows
    "Match" as part of a span's baseline inspector fields."""
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    page.wait_for_function(_EXACT_DOT, timeout=3000)


@pytest.mark.feature("UI-02")
def test_typing_new_text_updates_the_live_preview(page: Page, app_url: str, corpus: Corpus) -> None:
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    page.wait_for_function(_EXACT_DOT, timeout=3000)
    page.keyboard.type("Some Completely Different Text")
    page.wait_for_function(_EXACT_DOT, timeout=3000)  # still findable in the bundled/standard font
    match_text = page.eval_on_selector(".pw-inspector-row:last-child .pw-inspector-value", "el => el.textContent")
    assert "Exact" in match_text


@pytest.mark.feature("UI-02")
def test_escape_reverts_the_draft(page: Page, app_url: str, corpus: Corpus) -> None:
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    original = page.eval_on_selector(".pw-span-box", "el => el.textContent")
    page.keyboard.type("Should Not Be Saved")
    page.check("#pw-edit-bold")
    page.focus(_EDITOR)
    page.keyboard.press("Escape")
    assert _editor_value(page) == original
    assert page.is_checked("#pw-edit-bold") is False
    assert page.eval_on_selector(".pw-span-box", "el => el.textContent") == original
    assert _history_count(page) == 0


@pytest.mark.feature("UI-02")
def test_clicking_away_keeps_the_draft(page: Page, app_url: str, corpus: Corpus) -> None:
    """The owner's rule (2026-09-29): clicking elsewhere never throws an edit
    away. Clicking empty page, the toolbar or the panel keeps the selection
    and the typed draft; only Revert/Esc or choosing other text discards it."""
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    original = page.eval_on_selector(".pw-span-box", "el => el.textContent")
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.keyboard.type("Kept While I Look Around")
    page.click(".pw-page-area", position={"x": 5, "y": 5})
    page.click(".pw-inspector h2")
    page.wait_for_timeout(300)
    assert _editor_value(page) == "Kept While I Look Around"
    assert page.query_selector(_SELECTED) is not None
    assert page.eval_on_selector(".pw-span-box", "el => el.textContent") == original  # not applied yet
    assert _history_count(page) == 0
    assert errors == []


@pytest.mark.feature("UI-02")
def test_enter_commits_an_exact_match_without_a_confirmation_dialog(page: Page, app_url: str, corpus: Corpus) -> None:
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    page.wait_for_function(_EXACT_DOT, timeout=3000)
    page.keyboard.type("Hello, Editor.")
    page.wait_for_timeout(400)  # let the debounced preview resolve before committing
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-history-entry", timeout=5000)
    assert dialogs == []
    page.wait_for_function(
        "() => document.querySelector('.pw-span-box')?.textContent === 'Hello, Editor.'", timeout=5000
    )
    # The edited text stays selected, ready for the next change.
    page.wait_for_selector(_SELECTED, timeout=5000)
    assert _editor_value(page) == "Hello, Editor."


@pytest.mark.feature("UI-02")
def test_a_fallback_match_asks_for_confirmation_before_committing(page: Page, app_url: str, corpus: Corpus) -> None:
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    _open_and_click_first_span(page, app_url, str(corpus.type3))
    page.wait_for_function(_FALLBACK_DOT, timeout=3000)
    page.keyboard.type("X")
    page.wait_for_timeout(400)
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-history-entry", timeout=5000)
    assert len(dialogs) == 1
    assert "fallback" in dialogs[0]


@pytest.mark.feature("UI-02")
def test_declining_the_confirmation_leaves_the_edit_uncommitted(page: Page, app_url: str, corpus: Corpus) -> None:
    page.on("dialog", lambda dialog: dialog.dismiss())
    _open_and_click_first_span(page, app_url, str(corpus.type3))
    page.wait_for_function(_FALLBACK_DOT, timeout=3000)
    page.keyboard.type("X")
    page.wait_for_timeout(400)
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert page.query_selector(_SELECTED) is not None
    assert _editor_value(page) == "X"  # the draft is kept, to change or apply again
    assert _history_count(page) == 0


# -- UI-04 (history panel with undo/redo) --


def _commit_edit(page: Page, app_url: str, path: str, new_text: str) -> None:
    """Waits for a real, late effect of the commit's reload -- a history entry
    appearing -- not merely for the request to start. A caller that acts
    before the reload finishes can race a still-in-flight
    reloadDocument() -- confirmed the hard way, on
    Windows CI only, by a UI-05 test that toggled Compare and then
    check()/uncheck()ed its diff checkbox: a *second*, delayed
    compare.show() call (from the edit's own reload, only now completing,
    finding `comparing` already true) reset the checkbox out from under
    the test mid-sequence. Playwright's own page.click() has an
    actionability wait that happens to absorb this race for a plain
    button click, which is why it went unnoticed in the UI-04 tests that
    click Undo/Redo right after this helper -- but nothing saves a
    stateful sequence, or a raw keyboard.press() with no target element to
    wait on (as UI-08's keyboard-shortcut tests use)."""
    _open_and_click_first_span(page, app_url, path)
    page.wait_for_function(_EXACT_DOT, timeout=3000)
    page.keyboard.type(new_text)
    page.wait_for_timeout(400)  # let the debounced preview resolve before committing
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-history-entry", timeout=5000)
    page.wait_for_selector(_SELECTED, timeout=5000)  # the reload finished and reselected it


_FIRST_THUMB = "document.querySelector('.pw-thumb canvas')"
_FIRST_THUMB_PAINTED = (
    f"() => {{ const c = {_FIRST_THUMB}; if (!c) return false;"
    " return c.getContext('2d').getImageData(0, 0, c.width, c.height).data.some((v, i) => i % 4 === 3 && v > 0); }"
)


@pytest.mark.feature("UI-01")
def test_an_edit_rerenders_the_thumbnail_in_place(page: Page, app_url: str, corpus: Corpus) -> None:
    """A committed edit re-renders the changed page's thumbnail on the same
    button element, instead of tearing down and rebuilding the whole rail
    (which also leaked the old IntersectionObserver)."""
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_overlay_ready(page)
    page.wait_for_function(_FIRST_THUMB_PAINTED, timeout=5000)
    page.evaluate(f"() => {{ const c = {_FIRST_THUMB}; c.dataset.kept = '1'; window.__before = c.toDataURL(); }}")

    page.click(".pw-span-box")
    page.wait_for_selector(_SELECTED, timeout=3000)
    page.wait_for_function(_EXACT_DOT, timeout=3000)
    page.keyboard.type("Hello, Editor.")
    page.wait_for_timeout(400)  # let the debounced preview resolve before committing
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-history-entry", timeout=5000)

    page.wait_for_function(f"() => {_FIRST_THUMB}.toDataURL() !== window.__before", timeout=5000)
    assert page.evaluate(f"() => {_FIRST_THUMB}.dataset.kept") == "1"


def _undo_button_disabled(page: Page) -> bool:
    return bool(page.eval_on_selector(".pw-history button:has-text('Undo')", "el => el.disabled"))


def _redo_button_disabled(page: Page) -> bool:
    return bool(page.eval_on_selector(".pw-history button:has-text('Redo')", "el => el.disabled"))


@pytest.mark.feature("UI-04")
def test_history_panel_shows_no_edits_yet_before_any_commit(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_viewer_ready(page)
    page.wait_for_selector(".pw-history-empty", timeout=5000)
    assert _undo_button_disabled(page) is True
    assert _redo_button_disabled(page) is True


@pytest.mark.feature("UI-04")
def test_history_panel_shows_an_entry_after_a_committed_edit(page: Page, app_url: str, corpus: Corpus) -> None:
    _commit_edit(page, app_url, str(corpus.simple), "Hello, Editor.")
    assert "Hello, Editor." in (page.text_content(".pw-history-entry") or "")
    assert _undo_button_disabled(page) is False
    assert _redo_button_disabled(page) is True


@pytest.mark.feature("UI-04")
def test_undo_reverts_the_content_and_updates_button_state(page: Page, app_url: str, corpus: Corpus) -> None:
    """corpus.simple's original text -- see test_inspector_shows_the_selected_spans_style's
    docstring above -- is "Hello, PDFWorkerz."."""
    _commit_edit(page, app_url, str(corpus.simple), "Hello, Editor.")
    page.click(".pw-history button:has-text('Undo')")
    page.wait_for_function(
        "() => document.querySelector('.pw-span-box')?.textContent === 'Hello, PDFWorkerz.'", timeout=5000
    )
    page.wait_for_selector(".pw-history-empty", timeout=5000)
    assert _undo_button_disabled(page) is True
    assert _redo_button_disabled(page) is False


@pytest.mark.feature("UI-04")
def test_redo_reapplies_the_content_and_updates_button_state(page: Page, app_url: str, corpus: Corpus) -> None:
    _commit_edit(page, app_url, str(corpus.simple), "Hello, Editor.")
    page.click(".pw-history button:has-text('Undo')")
    page.wait_for_selector(".pw-history-empty", timeout=5000)
    page.click(".pw-history button:has-text('Redo')")
    page.wait_for_function(
        "() => document.querySelector('.pw-span-box')?.textContent === 'Hello, Editor.'", timeout=5000
    )
    page.wait_for_selector(".pw-history-entry", timeout=5000)
    assert _undo_button_disabled(page) is False
    assert _redo_button_disabled(page) is True


# -- UI-05 (before/after split view) --

_COMPARE_STATUS_READY = (
    "() => { const t = document.querySelector('.pw-compare-status')?.textContent; return !!t && t !== 'Comparing…'; }"
)


def _toggle_compare(page: Page) -> None:
    page.click(".pw-toolbar button:has-text('Compare')")
    page.wait_for_function(_COMPARE_STATUS_READY, timeout=5000)


@pytest.mark.feature("UI-05")
def test_compare_toggle_shows_both_renders_and_reports_identical_before_any_edit(
    page: Page, app_url: str, corpus: Corpus
) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_viewer_ready(page)

    _toggle_compare(page)
    assert page.eval_on_selector_all(".pw-compare-img", "els => els.length") == 2
    assert "identical" in (page.text_content(".pw-compare-status") or "")


@pytest.mark.feature("UI-05")
def test_compare_reports_differing_pages_after_a_committed_edit(page: Page, app_url: str, corpus: Corpus) -> None:
    _commit_edit(page, app_url, str(corpus.simple), "Hello, Editor.")
    _toggle_compare(page)
    assert "differ" in (page.text_content(".pw-compare-status") or "")


@pytest.mark.feature("UI-05")
def test_diff_overlay_toggle_shows_and_hides_the_overlay_canvas(page: Page, app_url: str, corpus: Corpus) -> None:
    _commit_edit(page, app_url, str(corpus.simple), "Hello, Editor.")
    _toggle_compare(page)

    diff_checkbox = page.locator(".pw-compare-diff-label input[type=checkbox]")
    assert diff_checkbox.is_enabled()
    assert page.eval_on_selector(".pw-compare-diff-overlay", "el => el.hidden") is True

    diff_checkbox.check()
    assert page.eval_on_selector(".pw-compare-diff-overlay", "el => el.hidden") is False

    diff_checkbox.uncheck()
    assert page.eval_on_selector(".pw-compare-diff-overlay", "el => el.hidden") is True


@pytest.mark.feature("UI-05")
def test_toggling_compare_off_returns_to_the_editable_canvas(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_viewer_ready(page)

    _toggle_compare(page)
    assert page.eval_on_selector_all(".pw-canvas-wrap", "els => els.length") == 0

    page.click(".pw-toolbar button:has-text('Compare')")
    page.wait_for_selector(".pw-canvas-wrap", timeout=5000)
    assert page.eval_on_selector_all(".pw-compare", "els => els.length") == 0


# -- UI-08 (keyboard shortcuts and accessible UI) --


@pytest.mark.feature("UI-08")
def test_arrow_keys_move_the_caret_instead_of_navigating_pages_while_editing(
    page: Page, app_url: str, corpus: Corpus
) -> None:
    """Regression test for a real bug found while adding the shortcuts
    below: arrow keys pressed while typing also navigated pages, and (via
    the handler's own preventDefault) silently broke caret movement. The
    text is typed in the inspector's textarea now; the guard must still hold."""
    _open_and_click_first_span(page, app_url, str(corpus.multi_page))
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    assert page.text_content(".pw-page-indicator", timeout=1000).startswith("1 /")
    assert page.query_selector(_SELECTED) is not None


@pytest.mark.feature("UI-08")
def test_home_and_end_keys_jump_to_first_and_last_page(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.multi_page))
    _wait_viewer_ready(page)

    page.keyboard.press("End")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim()"
        f" === '{corpus.multi_page_count} / {corpus.multi_page_count}'",
        timeout=5000,
    )
    page.keyboard.press("Home")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim().startsWith('1 /')", timeout=5000
    )


@pytest.mark.feature("UI-08")
def test_plus_and_minus_keys_zoom_in_and_out(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_viewer_ready(page)

    before = page.eval_on_selector(".pw-page-area canvas", "el => el.width")
    page.keyboard.press("+")
    page.wait_for_function(f"() => document.querySelector('.pw-page-area canvas')?.width > {before}", timeout=5000)
    zoomed_in = page.eval_on_selector(".pw-page-area canvas", "el => el.width")
    page.keyboard.press("-")
    page.wait_for_function(f"() => document.querySelector('.pw-page-area canvas')?.width < {zoomed_in}", timeout=5000)


@pytest.mark.feature("UI-08")
def test_ctrl_z_undoes_and_ctrl_shift_z_redoes(page: Page, app_url: str, corpus: Corpus) -> None:
    _commit_edit(page, app_url, str(corpus.simple), "Hello, Editor.")
    page.keyboard.press("Control+z")
    page.wait_for_function(
        "() => document.querySelector('.pw-span-box')?.textContent === 'Hello, PDFWorkerz.'", timeout=5000
    )
    # reloadDocument() re-renders the page (the wait above) *before* it
    # refreshes the history panel -- which is what actually re-enables the
    # Redo button. Pressing Ctrl+Shift+Z in that gap would be a silent
    # no-op (triggerRedo() is exactly a disabled button's click()), same
    # race _commit_edit's own wait guards against, one step later. Waiting
    # for the settled empty-history state (as the UI-04 undo test already
    # does) closes that gap before the next keyboard-only action.
    page.wait_for_selector(".pw-history-empty", timeout=5000)
    page.keyboard.press("Control+Shift+z")
    page.wait_for_function(
        "() => document.querySelector('.pw-span-box')?.textContent === 'Hello, Editor.'", timeout=5000
    )


@pytest.mark.feature("UI-08")
def test_c_key_toggles_the_compare_view(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_viewer_ready(page)

    page.keyboard.press("c")
    page.wait_for_function(_COMPARE_STATUS_READY, timeout=5000)
    assert page.eval_on_selector_all(".pw-canvas-wrap", "els => els.length") == 0

    page.keyboard.press("c")
    page.wait_for_selector(".pw-canvas-wrap", timeout=5000)
    assert page.eval_on_selector_all(".pw-compare", "els => els.length") == 0


@pytest.mark.feature("UI-08")
def test_question_mark_opens_the_shortcuts_list_and_escape_closes_it(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.multi_page))
    _wait_viewer_ready(page)

    page.keyboard.press("?")
    page.wait_for_selector(".pw-shortcuts[open]", timeout=5000)
    listed = page.text_content(".pw-shortcuts") or ""
    for keys in ("Ctrl+Z", "Home / End", "Page Down"):
        assert keys in listed, keys
    # While the list is open it owns the keyboard: End must not navigate.
    page.keyboard.press("End")
    assert (page.text_content(".pw-page-indicator") or "").strip().startswith("1 /")

    page.keyboard.press("Escape")
    page.wait_for_selector(".pw-shortcuts[open]", state="detached", timeout=5000)
    page.click(".pw-shortcuts-toggle")
    page.wait_for_selector(".pw-shortcuts[open]", timeout=5000)
    page.click(".pw-shortcuts button[type=submit]")
    page.wait_for_selector(".pw-shortcuts[open]", state="detached", timeout=5000)


# -- UI-09 (light/dark theme toggle with persistence) --

_THEME_ATTR = "() => document.documentElement.dataset.theme ?? null"


@pytest.mark.feature("UI-09")
def test_theme_toggle_is_visible_before_any_document_is_open(page: Page, connect_only_url: str) -> None:
    """The toggle lives outside app.ts's screen-swapping (main.ts mounts it
    into a sibling of #app, never #app itself), so it's there on the very
    first screen, before a session is even configured -- not just once a
    document is open."""
    page.goto(connect_only_url)
    page.wait_for_selector(".pw-theme-toggle", timeout=5000)


@pytest.mark.feature("UI-09")
def test_theme_toggle_cycles_through_auto_light_and_dark(page: Page, connect_only_url: str) -> None:
    page.goto(connect_only_url)
    page.wait_for_selector(".pw-theme-toggle", timeout=5000)
    assert page.evaluate(_THEME_ATTR) is None  # "system" -- no explicit override yet

    page.click(".pw-theme-toggle")
    assert page.evaluate(_THEME_ATTR) == "light"

    page.click(".pw-theme-toggle")
    assert page.evaluate(_THEME_ATTR) == "dark"

    page.click(".pw-theme-toggle")
    assert page.evaluate(_THEME_ATTR) is None  # back to "system"


@pytest.mark.feature("UI-09")
def test_choosing_dark_actually_changes_the_rendered_colors(page: Page, connect_only_url: str) -> None:
    """Not just that the attribute gets set -- that style.css's rules for
    it actually take effect, proven by a real computed style change."""
    page.goto(connect_only_url)
    page.wait_for_selector(".pw-theme-toggle", timeout=5000)
    light_bg = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()")

    page.click(".pw-theme-toggle")  # -> light (explicit, same as default here, but exercises the path)
    page.click(".pw-theme-toggle")  # -> dark
    dark_bg = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()")

    assert dark_bg != light_bg


@pytest.mark.feature("UI-09")
def test_theme_choice_persists_across_a_reload(page: Page, connect_only_url: str) -> None:
    page.goto(connect_only_url)
    page.wait_for_selector(".pw-theme-toggle", timeout=5000)
    page.click(".pw-theme-toggle")  # -> light
    assert page.evaluate(_THEME_ATTR) == "light"

    page.reload()
    page.wait_for_selector(".pw-theme-toggle", timeout=5000)
    assert page.evaluate(_THEME_ATTR) == "light"


def _span_box_font_weight(page: Page, text: str) -> str:
    return str(
        page.evaluate(
            "t => [...document.querySelectorAll('.pw-span-box')].find(b => b.textContent === t)?.style.fontWeight",
            text,
        )
    )


@pytest.mark.feature("EDT-07")
def test_format_painter_copies_a_style_onto_clicked_text(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.bold_italic_standard))
    _wait_overlay_ready(page)
    assert _span_box_font_weight(page, "Regular text") == "normal"

    page.click(".pw-span-box:has-text('Bold text')")
    page.wait_for_selector(_SELECTED, timeout=3000)
    page.click(".pw-copy-style")
    page.wait_for_selector(".pw-painter-status:not([hidden])", timeout=3000)
    assert page.query_selector(_SELECTED) is None  # arming ends the selection

    page.click(".pw-span-box:has-text('Regular text')")
    page.wait_for_selector(".pw-history-entry:has-text('Copy style')", timeout=10000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('.pw-span-box')]"
        ".find(b => b.textContent === 'Regular text')?.style.fontWeight === 'bold'",
        timeout=5000,
    )
    assert page.is_hidden(".pw-painter-status")


@pytest.mark.feature("EDT-07")
def test_escape_cancels_an_armed_format_painter(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.bold_italic_standard))
    _wait_overlay_ready(page)
    page.click(".pw-span-box:has-text('Bold text')")
    page.click(".pw-copy-style")
    page.wait_for_selector(".pw-painter-status:not([hidden])", timeout=3000)

    page.keyboard.press("Escape")
    page.wait_for_selector(".pw-painter-status", state="hidden", timeout=3000)
    # With the painter gone, a click selects again instead of painting.
    page.click(".pw-span-box:has-text('Regular text')")
    page.wait_for_selector(_SELECTED, timeout=3000)
    assert page.query_selector(".pw-history-entry") is None


def _select_first_span_on(page: Page, app_url: str, path: str) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, path)
    _wait_overlay_ready(page)
    page.click(".pw-span-box")
    page.wait_for_selector(_SELECTED, timeout=3000)


@pytest.mark.feature("EDT-10")
def test_add_edit_and_remove_a_link_from_the_inspector(page: Page, app_url: str, corpus: Corpus) -> None:
    _select_first_span_on(page, app_url, str(corpus.multi_page))  # several pages, so "2" is a valid target
    assert page.query_selector(".pw-link-item") is None

    page.once("dialog", lambda dialog: dialog.accept("https://example.com/docs"))
    page.click(".pw-add-link")
    page.wait_for_selector(".pw-link-box", timeout=10000)  # drawn on the page after the reload
    page.wait_for_selector(".pw-history-entry:has-text('Add link')", timeout=5000)

    page.click(".pw-span-box")
    page.wait_for_selector(".pw-link-item:has-text('https://example.com/docs')", timeout=3000)
    page.once("dialog", lambda dialog: dialog.accept("2"))
    page.click(".pw-link-edit")
    page.wait_for_selector(".pw-history-entry:has-text('Edit link')", timeout=10000)

    page.click(".pw-span-box")
    page.wait_for_selector(".pw-link-item:has-text('page 2')", timeout=3000)
    page.click(".pw-link-remove")
    page.wait_for_selector(".pw-history-entry:has-text('Remove link')", timeout=10000)
    page.wait_for_selector(".pw-link-box", state="detached", timeout=5000)


@pytest.mark.feature("EDT-10")
def test_an_unsafe_link_is_refused_with_the_engines_message(page: Page, app_url: str, corpus: Corpus) -> None:
    _select_first_span_on(page, app_url, str(corpus.simple))
    messages: list[str] = []

    def handle(dialog: object) -> None:
        # the prompt, then the alert carrying the engine's refusal
        if dialog.type == "prompt":  # type: ignore[attr-defined]
            dialog.accept("javascript:alert(1)")  # type: ignore[attr-defined]
        else:
            messages.append(dialog.message)  # type: ignore[attr-defined]
            dialog.dismiss()  # type: ignore[attr-defined]

    page.on("dialog", handle)
    page.click(".pw-add-link")
    page.wait_for_function("() => true")  # let the dialog round trip settle
    for _ in range(50):
        if messages:
            break
        page.wait_for_timeout(100)
    assert messages and "scheme" in messages[0]
    assert page.query_selector(".pw-link-box") is None


def _drag(page: Page, selector: str, dx: float, dy: float) -> None:
    box = page.locator(selector).bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y + dy / 2)
    page.mouse.move(x + dx, y + dy)
    page.mouse.up()


def _use_block_mode(page: Page) -> None:
    """EDT-16: the paragraph handles (move + resize, `move_text_block`) belong to Block
    mode; the default, Line, moves one line. Set before the app loads, as a returning
    user's remembered choice would be."""
    page.add_init_script("window.localStorage.setItem('pdfworkerz.selectMode', 'block')")


_PARAGRAPH_BOX = ".pw-span-box:has-text('line one')"
_LINE_THREE = "And this is line three, the last."


def _paragraph_rect(page: Page) -> dict[str, float]:
    """The box of the block holding the fixture's three-line paragraph (layer pixels)."""
    return dict(
        page.eval_on_selector(
            _PARAGRAPH_BOX, "el => ({ top: parseFloat(el.style.top), height: parseFloat(el.style.height) })"
        )
    )


@pytest.mark.feature("EDT-05")
def test_dragging_the_move_handle_moves_the_whole_paragraph(page: Page, app_url: str, corpus: Corpus) -> None:
    _use_block_mode(page)
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.paragraph))
    _wait_overlay_ready(page)
    # One box for the whole paragraph: all three lines move together.
    assert _LINE_THREE in (page.text_content(_PARAGRAPH_BOX) or "")
    before = _paragraph_rect(page)["top"]

    page.click(_PARAGRAPH_BOX)
    page.wait_for_selector(".pw-move-handle", timeout=3000)
    _drag(page, ".pw-move-handle", 0, 300)

    page.wait_for_selector(".pw-history-entry:has-text('Move paragraph')", timeout=10000)
    page.wait_for_function(
        "([t, before]) => [...document.querySelectorAll('.pw-span-box.pw-unit-block')]"
        ".some(b => b.textContent.includes(t) && parseFloat(b.style.top) > before + 250)",
        arg=[_LINE_THREE, before],
        timeout=5000,
    )


@pytest.mark.feature("EDT-05")
def test_dragging_the_resize_handle_rewraps_the_paragraph(page: Page, app_url: str, corpus: Corpus) -> None:
    _use_block_mode(page)
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.paragraph))
    _wait_overlay_ready(page)
    height_before = _paragraph_rect(page)["height"]

    page.click(_PARAGRAPH_BOX)
    page.wait_for_selector(".pw-resize-handle", timeout=3000)
    handle_width = page.locator(_PARAGRAPH_BOX).bounding_box()
    assert handle_width is not None
    _drag(page, ".pw-resize-handle", -handle_width["width"] / 2, 0)

    page.wait_for_selector(".pw-history-entry:has-text('Resize paragraph')", timeout=10000)
    # Half as wide, so it wraps onto more lines: the paragraph's box gets taller.
    page.wait_for_function(
        "([t, before]) => [...document.querySelectorAll('.pw-span-box.pw-unit-block')]"
        ".some(b => b.textContent.includes(t) && parseFloat(b.style.height) > before * 1.4)",
        arg=["line one", height_before],
        timeout=5000,
    )


@pytest.mark.feature("EDT-05")
def test_a_click_on_a_handle_without_dragging_changes_nothing(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.paragraph))
    _wait_overlay_ready(page)
    page.click(".pw-span-box:has-text('line one')")
    page.click(".pw-move-handle")
    page.wait_for_timeout(500)
    assert page.query_selector(".pw-history-entry") is None
    assert page.query_selector(_SELECTED) is not None  # the selection survived the press


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    """One page with one 200x150pt image and a caption."""
    import io

    import pymupdf
    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 160, 0)).save(png, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(100, 100, 300, 250), stream=png.getvalue())
    page.insert_text((100, 300), "Figure caption", fontsize=12)
    path = tmp_path / "with_image.pdf"
    doc.save(path)
    return path


@pytest.fixture
def small_png(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "small.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(path)
    return path


def _open_image_pdf(page: Page, app_url: str, path: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(path))
    _wait_overlay_ready(page)
    page.wait_for_selector(".pw-image-box", timeout=5000)


def _image_box_width_below(page: Page, limit: float) -> None:
    page.wait_for_function(
        "limit => (document.querySelector('.pw-image-box')?.getBoundingClientRect().width ?? 1e9) < limit",
        arg=limit,
        timeout=5000,
    )


def _box(page: Page, selector: str) -> dict[str, float]:
    box = page.locator(selector).first.bounding_box()
    assert box is not None
    return box


@pytest.mark.feature("EDT-08")
def test_selecting_an_image_shows_it_in_the_inspector_and_delete_removes_it(
    page: Page, app_url: str, image_pdf: Path
) -> None:
    _open_image_pdf(page, app_url, image_pdf)
    page.click(".pw-image-box")
    page.wait_for_selector(".pw-image-section:not([hidden])", timeout=3000)
    assert "40 \u00d7 30" in (page.text_content(".pw-image-section") or "")

    page.click(".pw-image-delete")
    page.wait_for_selector(".pw-history-entry:has-text('Delete image')", timeout=10000)
    page.wait_for_selector(".pw-image-box", state="detached", timeout=5000)
    assert page.query_selector(".pw-span-box:has-text('Figure caption')") is not None


@pytest.mark.feature("EDT-08")
def test_dragging_an_image_moves_it(page: Page, app_url: str, image_pdf: Path) -> None:
    _open_image_pdf(page, app_url, image_pdf)
    before = _box(page, ".pw-image-box")
    _drag(page, ".pw-image-box", 120, 200)
    page.wait_for_selector(".pw-history-entry:has-text('Move/resize image')", timeout=10000)
    page.wait_for_function(
        f"() => {{ const b = document.querySelector('.pw-image-box')?.getBoundingClientRect();"
        f" return b && b.top > {before['y'] + 150}; }}",
        timeout=5000,
    )


@pytest.mark.feature("EDT-08")
def test_dragging_the_corner_resizes_an_image(page: Page, app_url: str, image_pdf: Path) -> None:
    _open_image_pdf(page, app_url, image_pdf)
    before = _box(page, ".pw-image-box")
    page.click(".pw-image-box")
    page.wait_for_selector(".pw-object-resize", timeout=3000)
    _drag(page, ".pw-object-resize", -before["width"] / 2, -before["height"] / 2)
    page.wait_for_selector(".pw-history-entry:has-text('Move/resize image')", timeout=10000)
    _image_box_width_below(page, before["width"] * 0.7)


@pytest.mark.feature("EDT-08")
def test_crop_mode_cuts_the_image_to_the_dragged_area(page: Page, app_url: str, image_pdf: Path) -> None:
    _open_image_pdf(page, app_url, image_pdf)
    before = _box(page, ".pw-image-box")
    page.click(".pw-image-box")
    page.click(".pw-image-crop")
    x, y = before["x"] + before["width"] * 0.1, before["y"] + before["height"] * 0.1
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + before["width"] * 0.25, y + before["height"] * 0.25)
    page.mouse.move(x + before["width"] * 0.4, y + before["height"] * 0.4)
    page.mouse.up()
    page.wait_for_selector(".pw-history-entry:has-text('Crop image')", timeout=10000)
    _image_box_width_below(page, before["width"] * 0.5)


@pytest.mark.feature("EDT-08")
def test_replace_and_insert_go_through_a_file_picker(
    page: Page, app_url: str, image_pdf: Path, small_png: Path
) -> None:
    _open_image_pdf(page, app_url, image_pdf)
    page.click(".pw-image-box")
    with page.expect_file_chooser() as chooser:
        page.click(".pw-image-replace")
    chooser.value.set_files(str(small_png))
    page.wait_for_selector(".pw-history-entry:has-text('Replace image')", timeout=10000)

    with page.expect_file_chooser() as chooser:
        page.click(".pw-insert-image")
    chooser.value.set_files(str(small_png))
    page.wait_for_selector(".pw-history-entry:has-text('Insert image')", timeout=10000)
    page.wait_for_function("() => document.querySelectorAll('.pw-image-box').length === 2", timeout=5000)


@pytest.mark.feature("EDT-08")
def test_text_over_an_image_is_still_editable(page: Page, app_url: str, tmp_path: Path) -> None:
    import io

    import pymupdf
    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (40, 30), (230, 230, 230)).save(png, format="PNG")
    doc = pymupdf.open()
    pdf_page = doc.new_page()
    pdf_page.insert_image(pymupdf.Rect(80, 80, 400, 200), stream=png.getvalue())
    pdf_page.insert_text((100, 140), "Label on image", fontsize=14)
    path = tmp_path / "text_on_image.pdf"
    doc.save(path)

    _open_image_pdf(page, app_url, path)
    span = _box(page, ".pw-span-box:has-text('Label on image')")
    page.mouse.click(span["x"] + span["width"] / 2, span["y"] + span["height"] / 2)
    page.wait_for_selector(_SELECTED, timeout=3000)


@pytest.fixture
def shape_pdf(tmp_path: Path) -> Path:
    """One page with a stroked rectangle and a caption."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(100, 100, 300, 250))
    shape.finish(color=(0, 0, 1), width=2)
    shape.commit()
    page.insert_text((100, 300), "Shape caption", fontsize=12)
    path = tmp_path / "with_shape.pdf"
    doc.save(path)
    return path


def _open_shape_pdf(page: Page, app_url: str, path: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(path))
    _wait_overlay_ready(page)
    page.wait_for_selector(".pw-shape-box", timeout=5000)


@pytest.mark.feature("EDT-09")
def test_draw_mode_draws_a_rectangle_by_dragging(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_overlay_ready(page)
    page.select_option(".pw-draw-select", "rect")
    canvas = _box(page, ".pw-canvas-wrap canvas")
    area = _box(page, ".pw-page-area")  # the visible part of the page: the canvas runs on below it
    x, y = canvas["x"] + canvas["width"] * 0.3, max(canvas["y"], area["y"]) + 150
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 60, y + 40)
    page.mouse.move(x + 120, y + 80)
    page.mouse.up()
    page.wait_for_selector(".pw-history-entry:has-text('Draw rect')", timeout=10000)
    page.wait_for_selector(".pw-shape-box", timeout=5000)

    page.keyboard.press("Escape")  # leaves draw mode: text is clickable again
    page.wait_for_selector(".pw-draw-surface", state="detached", timeout=3000)
    page.click(".pw-span-box")
    page.wait_for_selector(_SELECTED, timeout=3000)


@pytest.mark.feature("EDT-09")
def test_select_restyle_and_delete_a_shape(page: Page, app_url: str, shape_pdf: Path) -> None:
    _open_shape_pdf(page, app_url, shape_pdf)
    page.click(".pw-shape-box")
    page.wait_for_selector(".pw-shape-section:not([hidden])", timeout=3000)
    assert page.input_value(".pw-shape-stroke") == "#0000ff"
    assert page.is_checked(".pw-shape-no-fill")

    page.fill(".pw-shape-stroke", "#ff0000")
    page.uncheck(".pw-shape-no-fill")
    page.fill(".pw-shape-width", "4")
    page.click(".pw-shape-apply")
    page.wait_for_selector(".pw-history-entry:has-text('Edit shape')", timeout=10000)

    page.click(".pw-shape-box")
    page.wait_for_selector(".pw-shape-section:not([hidden])", timeout=3000)
    assert page.input_value(".pw-shape-stroke") == "#ff0000"
    assert not page.is_checked(".pw-shape-no-fill")

    page.click(".pw-shape-delete")
    page.wait_for_selector(".pw-history-entry:has-text('Delete shape')", timeout=10000)
    page.wait_for_selector(".pw-shape-box", state="detached", timeout=5000)
    assert page.query_selector(".pw-span-box:has-text('Shape caption')") is not None


@pytest.mark.feature("EDT-09")
def test_dragging_a_shape_moves_it(page: Page, app_url: str, shape_pdf: Path) -> None:
    _open_shape_pdf(page, app_url, shape_pdf)
    before = _box(page, ".pw-shape-box")
    _drag(page, ".pw-shape-box", 80, 250)
    page.wait_for_selector(".pw-history-entry:has-text('Edit shape')", timeout=10000)
    page.wait_for_function(
        "limit => (document.querySelector('.pw-shape-box')?.getBoundingClientRect().top ?? 0) > limit",
        arg=before["y"] + 200,
        timeout=5000,
    )


@pytest.fixture
def typo_pdf(tmp_path: Path) -> Path:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "We recieve teh report.", fontsize=14, fontname="helv")
    path = tmp_path / "typos.pdf"
    doc.save(path)
    return path


@pytest.mark.feature("EDT-11")
def test_spelling_toggle_underlines_misspelled_words(page: Page, app_url: str, typo_pdf: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(typo_pdf))
    _wait_overlay_ready(page)
    assert page.query_selector(".pw-misspelling") is None  # off by default

    page.click(".pw-spell-toggle")
    page.wait_for_selector(".pw-misspelling", timeout=10000)
    words = page.eval_on_selector_all(".pw-misspelling", "els => els.map(el => el.dataset.word)")
    assert words == ["recieve", "teh"]
    assert "(2)" in (page.text_content(".pw-spell-toggle") or "")

    page.click(".pw-spell-toggle")
    page.wait_for_selector(".pw-misspelling", state="detached", timeout=3000)


@pytest.mark.feature("EDT-11")
def test_choosing_a_suggestion_corrects_the_word(page: Page, app_url: str, typo_pdf: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(typo_pdf))
    _wait_overlay_ready(page)
    page.click(".pw-spell-toggle")
    page.wait_for_selector(".pw-misspelling[data-word='recieve']", timeout=10000)

    page.click(".pw-misspelling[data-word='recieve']")
    page.click(".pw-spell-suggestion:has-text('receive')")
    page.wait_for_selector(".pw-history-entry:has-text('Correct')", timeout=10000)
    page.wait_for_selector(".pw-span-box:has-text('We receive teh report.')", timeout=5000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('.pw-misspelling')].map(e => e.dataset.word).join() === 'teh'",
        timeout=10000,
    )


_MARKED_WORDS = "() => [...document.querySelectorAll('.pw-misspelling')].map(e => e.dataset.word).join() === '{}'"


def _open_typos_with_spelling_on(page: Page, app_url: str, typo_pdf: Path, first_word: str) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(typo_pdf))
    _wait_overlay_ready(page)
    page.click(".pw-spell-toggle")
    page.wait_for_selector(f".pw-misspelling[data-word='{first_word}']", timeout=10000)


@pytest.mark.feature("EDT-11")
def test_ignore_hides_a_word_and_changes_nothing_in_the_document(page: Page, app_url: str, typo_pdf: Path) -> None:
    _open_typos_with_spelling_on(page, app_url, typo_pdf, "teh")
    page.click(".pw-misspelling[data-word='teh']")
    page.click(".pw-spell-ignore")
    # Refreshing clears every mark first, so wait for the redrawn state, not just for "teh" to go.
    page.wait_for_function(_MARKED_WORDS.format("recieve"), timeout=10000)
    assert page.query_selector(".pw-history-entry") is None  # ignoring changes nothing in the document


@pytest.mark.feature("EDT-11")
def test_ignored_words_persist_across_a_reload_until_forgotten(page: Page, app_url: str, typo_pdf: Path) -> None:
    _open_typos_with_spelling_on(page, app_url, typo_pdf, "teh")
    page.click(".pw-misspelling[data-word='teh']")
    page.click(".pw-spell-ignore")
    page.wait_for_function(_MARKED_WORDS.format("recieve"), timeout=10000)

    # A fresh load of the app in the same browser: the ignore list comes back from localStorage.
    _open_typos_with_spelling_on(page, app_url, typo_pdf, "recieve")
    page.wait_for_function(_MARKED_WORDS.format("recieve"), timeout=10000)

    page.click(".pw-misspelling[data-word='recieve']")
    page.click(".pw-spell-forget:has-text('(1)')")
    page.wait_for_function(_MARKED_WORDS.format("recieve,teh"), timeout=10000)


# -- Saving from the browser (independent review #8) --


@pytest.fixture
def simple_copy(corpus: Corpus, tmp_path: Path) -> Path:
    import shutil

    path = tmp_path / "simple.pdf"
    shutil.copy(corpus.simple, path)
    return path


@pytest.mark.feature("UI-01")
def test_save_writes_the_edits_to_a_new_file_next_to_the_original(page: Page, app_url: str, simple_copy: Path) -> None:
    import pymupdf

    _commit_edit(page, app_url, str(simple_copy), "Hello, Editor.")
    page.click(".pw-save")
    page.wait_for_function(
        "() => document.querySelector('.pw-save-status')?.textContent?.startsWith('Saved to')", timeout=10000
    )
    saved = simple_copy.with_name("simple.edited.pdf")
    assert saved.exists()
    with pymupdf.open(saved) as doc:
        assert "Hello, Editor." in doc[0].get_text()
    with pymupdf.open(simple_copy) as original:
        assert "Hello, PDFWorkerz." in original[0].get_text()  # never overwritten


@pytest.mark.feature("UI-01")
def test_download_hands_the_browser_the_edited_pdf(page: Page, app_url: str, simple_copy: Path) -> None:
    _commit_edit(page, app_url, str(simple_copy), "Hello, Editor.")
    with page.expect_download() as info:
        page.click(".pw-download")
    download = info.value
    assert download.suggested_filename.endswith(".edited.pdf")
    assert Path(download.path()).read_bytes().startswith(b"%PDF-")


@pytest.mark.feature("UI-01")
def test_a_stale_session_token_is_not_reported_as_a_password_prompt(
    page: Page, app_url: str, simple_copy: Path
) -> None:
    page.goto(app_url.replace("token=", "token=stale-", 1))
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(simple_copy))
    page.wait_for_selector(".pw-error:not(:empty)", timeout=5000)
    assert "no longer valid" in (page.text_content(".pw-error") or "")


# -- Approval before a non-exact edit (independent review #9) --


@pytest.fixture
def unknown_font_typo_pdf(tmp_path: Path) -> Path:
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/TotallyUnknownFont")
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT /F1 14 Tf 72 700 Td (We recieve teh report.) Tj ET")
    path = tmp_path / "unknown_font.pdf"
    pdf.save(path)
    return path


@pytest.mark.feature("EDT-11")
def test_a_correction_that_cannot_use_the_exact_font_asks_first(
    page: Page, app_url: str, unknown_font_typo_pdf: Path
) -> None:
    messages: list[str] = []
    answers = iter([False, True])  # decline once, then accept

    def on_dialog(dialog: object) -> None:
        messages.append(dialog.message)  # type: ignore[attr-defined]
        if next(answers, True):
            dialog.accept()  # type: ignore[attr-defined]
        else:
            dialog.dismiss()  # type: ignore[attr-defined]

    page.on("dialog", on_dialog)
    _open_typos_with_spelling_on(page, app_url, unknown_font_typo_pdf, "recieve")

    page.click(".pw-misspelling[data-word='recieve']")
    page.click(".pw-spell-suggestion:has-text('receive')")
    for _ in range(100):  # the question comes after the refused "exact" attempt returns
        if messages:
            break
        page.wait_for_timeout(50)
    assert messages and "fallback" in messages[0]
    assert page.query_selector(".pw-history-entry") is None  # declined: nothing changed

    page.click(".pw-misspelling[data-word='recieve']")
    page.click(".pw-spell-suggestion:has-text('receive')")
    page.wait_for_selector(".pw-history-entry:has-text('Correct')", timeout=10000)


# -- EDT-06 change style / EDT-03 add text in an explicit style --


@pytest.mark.feature("EDT-06")
def test_change_style_makes_the_selected_text_bold(page: Page, app_url: str, simple_copy: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(simple_copy))
    _wait_overlay_ready(page)
    page.click(".pw-span-box")
    page.wait_for_selector(_SELECTED, timeout=3000)
    page.check("#pw-edit-bold")
    page.click("#pw-edit-apply")
    page.wait_for_selector(".pw-history-entry:has-text('bold')", timeout=10000)
    page.wait_for_function(
        "() => document.querySelector('.pw-span-box')?.textContent === 'Hello, PDFWorkerz.'", timeout=5000
    )
    page.click(".pw-span-box")
    page.wait_for_function(
        "() => (document.querySelector('.pw-inspector')?.textContent ?? '').includes('Helvetica-Bold')", timeout=5000
    )


@pytest.mark.feature("EDT-03")
def test_add_text_places_new_text_in_the_chosen_style(page: Page, app_url: str, simple_copy: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(simple_copy))
    _wait_overlay_ready(page)
    page.click(".pw-add-text")
    layer = page.query_selector(".pw-edit-layer")
    assert layer is not None
    box = layer.bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] * 0.2, box["y"] + 200)  # within the visible part of the page
    page.wait_for_selector(".pw-style-dialog[open]", timeout=5000)
    page.fill("#pw-style-text", "APPROVED")
    page.select_option("#pw-style-font", "Courier")
    page.fill("#pw-style-size", "20")
    page.click(".pw-style-submit")
    page.wait_for_selector(".pw-span-box:has-text('APPROVED')", timeout=10000)
    assert page.query_selector(".pw-history-entry") is not None
    page.click(".pw-span-box:has-text('APPROVED')")
    page.wait_for_function(
        "() => (document.querySelector('.pw-inspector')?.textContent ?? '').includes('Courier')", timeout=5000
    )


# -- P4: the command bar --


def _open_for_commands(page: Page, app_url: str, path: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(path))
    _wait_overlay_ready(page)


@pytest.mark.feature("CMD-04")
def test_command_bar_offers_what_can_come_next(page: Page, app_url: str, simple_copy: Path) -> None:
    _open_for_commands(page, app_url, simple_copy)
    page.keyboard.press("/")
    page.wait_for_function("() => document.activeElement?.id === 'pw-command'", timeout=3000)
    page.keyboard.type("rep")
    page.wait_for_selector(".pw-command-chip:has-text('replace')", timeout=5000)
    page.click(".pw-command-chip:has-text('replace')")
    assert page.input_value("#pw-command") == "replace "
    page.keyboard.type('"x" ')
    page.wait_for_selector(".pw-command-chip:has-text('with')", timeout=5000)


@pytest.mark.feature("CMD-05")
def test_command_bar_previews_then_applies_on_enter(page: Page, app_url: str, simple_copy: Path) -> None:
    _open_for_commands(page, app_url, simple_copy)
    page.click("#pw-command")
    page.keyboard.type('replace "PDFWorkerz" with "Editor"')
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-command-summary:has-text('1 match on page 1')", timeout=5000)
    assert page.query_selector(".pw-history-entry") is None  # previewing changes nothing
    page.click(".pw-command-apply")
    page.wait_for_selector(".pw-span-box:has-text('Hello, Editor.')", timeout=10000)
    page.wait_for_selector(".pw-history-entry", timeout=5000)


@pytest.mark.feature("CMD-06")
def test_command_bar_shows_did_you_mean_and_never_runs_the_guess(page: Page, app_url: str, simple_copy: Path) -> None:
    _open_for_commands(page, app_url, simple_copy)
    page.click("#pw-command")
    page.keyboard.type('repalce "PDFWorkerz" with "Editor"')
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-command-error", timeout=5000)
    assert page.query_selector(".pw-history-entry") is None
    page.click('.pw-command-suggestion:has-text(\'replace "PDFWorkerz" with "Editor"\')')
    assert page.input_value("#pw-command") == 'replace "PDFWorkerz" with "Editor"'


@pytest.mark.feature("CMD-07")
def test_recipe_export_and_run_from_the_command_bar(
    page: Page, app_url: str, simple_copy: Path, tmp_path: Path
) -> None:
    _open_for_commands(page, app_url, simple_copy)
    page.click("#pw-command")
    page.keyboard.type('replace "PDFWorkerz" with "Editor"')
    page.keyboard.press("Enter")
    page.click(".pw-command-apply")
    page.wait_for_selector(".pw-history-entry", timeout=10000)
    with page.expect_download() as info:
        page.click(".pw-recipe-export")
    recipe = tmp_path / "exported.yaml"
    info.value.save_as(recipe)
    assert "replace_text" in recipe.read_text(encoding="utf-8")

    page.click("#pw-command")
    page.keyboard.type("undo")
    page.keyboard.press("Enter")
    page.click(".pw-command-apply")
    page.wait_for_selector(".pw-span-box:has-text('Hello, PDFWorkerz.')", timeout=10000)
    with page.expect_file_chooser() as chooser:
        page.click(".pw-recipe-run")
    chooser.value.set_files(str(recipe))
    page.wait_for_selector(".pw-command-summary:has-text('1 match')", timeout=5000)
    page.click(".pw-command-apply")
    page.wait_for_selector(".pw-span-box:has-text('Hello, Editor.')", timeout=10000)


@pytest.mark.feature("CMD-05")
def test_apply_uses_the_page_that_was_previewed(page: Page, app_url: str, corpus: Corpus) -> None:
    _open_for_commands(page, app_url, corpus.multi_page)
    page.click("#pw-command")
    page.keyboard.type('insert "Checked" below "Page 1 of"')
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-command-summary:has-text('on page 1')", timeout=5000)
    page.click(".pw-toolbar button:has-text('Next')")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim().startsWith('2 /')", timeout=5000
    )
    page.click(".pw-command-apply")
    page.wait_for_selector(".pw-history-entry", timeout=10000)
    assert page.query_selector(".pw-span-box:has-text('Checked')") is None  # not on page 2
    page.click(".pw-toolbar button:has-text('Prev')")
    page.wait_for_selector(".pw-span-box:has-text('Checked')", timeout=10000)


@pytest.mark.feature("UI-01")
@pytest.mark.parametrize("width", [1400, 1000])
def test_the_viewer_fits_the_window_without_sideways_scrolling(
    browser: object, app_url: str, corpus: Corpus, width: int
) -> None:
    """Found in a real-scenario run: the toolbar didn't wrap, so new buttons pushed the page
    wider than the window, and the grid gave its flexible row to the command bar."""
    page = browser.new_page(viewport={"width": width, "height": 850})  # type: ignore[attr-defined]
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.simple))
    _wait_overlay_ready(page)
    assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
    toolbar = page.eval_on_selector(".pw-toolbar", "el => el.getBoundingClientRect().height")
    area = page.eval_on_selector(".pw-page-area", "el => el.getBoundingClientRect().height")
    assert area > toolbar * 3  # the page area, not the command bar, takes the spare height
    page.close()


@pytest.mark.feature("ORG-05")
def test_deleting_pages_updates_the_page_count_and_thumbnails(
    page: Page, app_url: str, corpus: Corpus, tmp_path: Path
) -> None:
    import shutil

    path = tmp_path / "multi.pdf"
    shutil.copy(corpus.multi_page, path)
    _open_for_commands(page, app_url, path)
    assert page.eval_on_selector_all(".pw-thumb", "els => els.length") == 5
    page.click("#pw-command")
    page.keyboard.type("delete pages 2-3")
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-command-summary:has-text('Delete page(s) 2, 3')", timeout=5000)
    page.click(".pw-command-apply")
    page.wait_for_function("() => document.querySelectorAll('.pw-thumb').length === 3", timeout=10000)
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim() === '1 / 3'", timeout=5000
    )
    page.click(".pw-history button:has-text('Undo')")
    page.wait_for_function("() => document.querySelectorAll('.pw-thumb').length === 5", timeout=10000)


def _thumb_is_landscape(page: Page, index: int) -> bool:
    return bool(
        page.eval_on_selector_all(
            ".pw-thumb canvas", f"els => els[{index}] && els[{index}].height < els[{index}].width"
        )
    )


@pytest.mark.feature("UI-07")
def test_dragging_and_alt_arrows_reorder_pages(page: Page, app_url: str, tmp_path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=842, height=595).insert_text((72, 72), "Wide page")  # only page 1 is landscape
    for n in range(2, 5):
        doc.new_page().insert_text((72, 72), f"Page {n}")
    path = tmp_path / "organize.pdf"
    doc.save(path)

    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(path))
    _wait_viewer_ready(page)
    page.wait_for_function(
        "() => { const c = document.querySelector('.pw-thumb canvas'); return c && c.height < c.width; }"
    )

    page.drag_and_drop(".pw-thumb:nth-child(1)", ".pw-thumb:nth-child(3)")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim() === '3 / 4'", timeout=5000
    )
    page.wait_for_function(
        "() => { const c = document.querySelectorAll('.pw-thumb canvas')[2]; return c.height < c.width; }", timeout=5000
    )
    assert not _thumb_is_landscape(page, 0)

    page.focus(".pw-thumb:nth-child(3)")
    page.keyboard.press("Alt+ArrowDown")
    page.wait_for_function(
        "() => document.querySelector('.pw-page-indicator')?.textContent?.trim() === '4 / 4'", timeout=5000
    )
    page.wait_for_function(
        "() => { const c = document.querySelectorAll('.pw-thumb canvas')[3]; return c.height < c.width; }", timeout=5000
    )


# -- UI-03: the inspector's text editor and the Fonts dialog --


@pytest.mark.feature("UI-03")
def test_text_and_style_apply_together_as_one_history_entry(page: Page, app_url: str, simple_copy: Path) -> None:
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    _open_and_click_first_span(page, app_url, str(simple_copy))
    page.fill(_EDITOR, "Hello, Bold Editor.")
    page.check("#pw-edit-bold")
    page.fill("#pw-edit-size", "16")
    assert page.is_enabled("#pw-edit-apply")
    page.click("#pw-edit-apply")
    page.wait_for_selector(".pw-history-entry", timeout=10000)
    entries = page.eval_on_selector_all(".pw-history-entry", "els => els.map(e => e.textContent)")
    assert len(entries) == 1
    assert "Hello, Bold Editor." in entries[0] and "bold" in entries[0] and "16pt" in entries[0]
    assert dialogs == []
    # Reselected with its new text and style, ready for the next change.
    page.wait_for_function(f"() => document.querySelector('{_EDITOR}')?.value === 'Hello, Bold Editor.'", timeout=5000)
    page.wait_for_function(
        "() => (document.querySelector('.pw-inspector')?.textContent ?? '').includes('Helvetica-Bold')", timeout=5000
    )
    assert page.input_value("#pw-edit-size") == "16"
    assert page.is_disabled("#pw-edit-apply")


@pytest.mark.feature("UI-03")
def test_choosing_other_text_with_an_unapplied_draft_asks_first(page: Page, app_url: str, corpus: Corpus) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(corpus.bold_italic_standard))
    _wait_overlay_ready(page)
    page.click(".pw-span-box:has-text('Bold text')")
    page.fill(_EDITOR, "My draft")

    page.once("dialog", lambda dialog: dialog.dismiss())  # keep it
    page.click(".pw-span-box:has-text('Regular text')")
    page.wait_for_timeout(200)
    assert _editor_value(page) == "My draft"
    assert page.query_selector(".pw-span-box.pw-span-selected:has-text('Bold text')") is not None

    page.once("dialog", lambda dialog: dialog.accept())  # discard it
    page.click(".pw-span-box:has-text('Regular text')")
    page.wait_for_function(f"() => document.querySelector('{_EDITOR}')?.value === 'Regular text'", timeout=3000)
    assert _history_count(page) == 0


@pytest.mark.feature("FNT-06")
def test_fonts_dialog_adds_a_font_from_the_open_document(page: Page, app_url: str, tmp_path: Path) -> None:
    from engine.fonts import research
    from tests.engine.test_font_library import _POSTSCRIPT, _pdf_with_font, _test_font

    source = _pdf_with_font(tmp_path / "full.pdf", _test_font())
    research.flag(_POSTSCRIPT, tier="approximate", note="closest metric match: a look-alike", document="full.pdf")
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(source))
    _wait_overlay_ready(page)

    page.click(".pw-fonts-toggle")
    page.wait_for_selector(f".pw-fonts-research tr[data-font='{_POSTSCRIPT}'] .pw-fonts-harvest", timeout=5000)
    assert page.query_selector(".pw-fonts-library td:has-text('No fonts added yet.')") is not None
    page.click(f".pw-fonts-research tr[data-font='{_POSTSCRIPT}'] .pw-fonts-harvest")
    page.wait_for_selector(f".pw-fonts-library tr[data-font='{_POSTSCRIPT}']", timeout=10000)
    assert "Added" in page.text_content(".pw-fonts-status")
    assert page.query_selector(".pw-fonts-research td:has-text('Nothing to research.')") is not None
    page.click(".pw-fonts-dialog button:has-text('Close')")

    # The editor's font list now offers it.
    page.click(".pw-span-box")
    page.wait_for_selector("#pw-edit-font option[value='PWTestSans']", state="attached", timeout=5000)


# -- EDT-13..15: selection, align, guides, nudge, copy/paste, delete --


@pytest.fixture
def arrange_pdf(tmp_path: Path) -> Path:
    """Two text blocks at different left edges, an image and three shapes."""
    import io

    import pymupdf
    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (30, 20), (40, 160, 90)).save(png, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 120), "Alpha heading", fontname="helv", fontsize=14)
    page.insert_text((230, 200), "Beta note", fontname="helv", fontsize=14)
    page.insert_image(pymupdf.Rect(330, 90, 390, 130), stream=png.getvalue())
    for x in (80, 150, 300):
        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(x, 300, x + 40, 340))
        shape.finish(color=(0, 0, 1), width=2)
        shape.commit()
    path = tmp_path / "arrange.pdf"
    doc.save(path)
    return path


def _open_arrange(page: Page, app_url: str, path: Path) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, str(path))
    _wait_overlay_ready(page)
    page.wait_for_selector(".pw-shape-box", timeout=5000)


def _shape_lefts(page: Page) -> list[float]:
    return sorted(page.eval_on_selector_all(".pw-shape-box", "els => els.map(e => e.getBoundingClientRect().left)"))


_BETA_LEFT = (
    "() => [...document.querySelectorAll('.pw-span-box')]"
    ".find(b => b.textContent === 'Beta note')?.getBoundingClientRect().left"
)


@pytest.mark.feature("EDT-05")
def test_edited_text_can_be_moved_without_an_approval_prompt(page: Page, app_url: str, arrange_pdf: Path) -> None:
    """The reported bug: after editing text, moving it asked to accept a look-alike font."""
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    _use_block_mode(page)
    _open_arrange(page, app_url, arrange_pdf)
    page.click(".pw-span-box:has-text('Alpha heading')")
    page.fill(_EDITOR, "Alpha title")
    page.keyboard.press("Enter")
    page.wait_for_selector(".pw-history-entry", timeout=10000)
    page.wait_for_selector(".pw-move-handle", timeout=5000)
    _drag(page, ".pw-move-handle", 0, 60)
    page.wait_for_selector(".pw-history-entry:has-text('Move paragraph')", timeout=10000)
    assert dialogs == []


@pytest.mark.feature("EDT-13")
def test_shift_click_selects_several_and_align_left_lines_them_up(page: Page, app_url: str, arrange_pdf: Path) -> None:
    _open_arrange(page, app_url, arrange_pdf)
    page.click(".pw-span-box:has-text('Alpha heading')")
    page.click(".pw-span-box:has-text('Beta note')", modifiers=["Shift"])
    page.click(".pw-image-box", modifiers=["Shift"])
    assert page.locator(".pw-arrange-outline").count() == 3
    assert page.is_enabled(".pw-align-select")
    alpha_left = page.locator(".pw-span-box:has-text('Alpha heading')").bounding_box()["x"]
    page.select_option(".pw-align-select", "left")
    page.wait_for_selector(".pw-history-entry:has-text('Move 2 objects')", timeout=10000)  # Alpha is leftmost
    page.wait_for_function(f"() => Math.abs(({_BETA_LEFT})() - {alpha_left}) < 2", timeout=5000)
    page.wait_for_function(
        f"() => Math.abs(document.querySelector('.pw-image-box').getBoundingClientRect().left - {alpha_left}) < 2",
        timeout=5000,
    )
    assert page.locator(".pw-arrange-outline").count() == 3  # still selected after the change


@pytest.mark.feature("EDT-13")
def test_marquee_selects_and_distribute_evens_the_gaps(page: Page, app_url: str, arrange_pdf: Path) -> None:
    _open_arrange(page, app_url, arrange_pdf)
    boxes = [page.locator(".pw-shape-box").nth(i).bounding_box() for i in range(3)]
    left = min(b["x"] for b in boxes) - 10
    top = min(b["y"] for b in boxes) - 10
    right = max(b["x"] + b["width"] for b in boxes) + 10
    bottom = max(b["y"] + b["height"] for b in boxes) + 10
    page.mouse.move(left, top)
    page.mouse.down()
    page.mouse.move((left + right) / 2, (top + bottom) / 2)
    page.mouse.move(right, bottom)
    page.mouse.up()
    assert page.locator(".pw-arrange-outline").count() == 3
    page.select_option(".pw-align-select", "distribute-h")
    page.wait_for_selector(".pw-history-entry:has-text('Move')", timeout=10000)
    page.wait_for_function(
        "() => { const r = [...document.querySelectorAll('.pw-shape-box')].map(e => e.getBoundingClientRect())"
        ".sort((a, b) => a.left - b.left);"
        " return r.length === 3 && Math.abs((r[1].left - r[0].right) - (r[2].left - r[1].right)) < 2; }",
        timeout=5000,
    )


@pytest.mark.feature("EDT-14")
def test_dragging_near_another_edge_snaps_and_shows_a_guide(page: Page, app_url: str, arrange_pdf: Path) -> None:
    _open_arrange(page, app_url, arrange_pdf)
    image = page.locator(".pw-image-box").bounding_box()
    target_left = page.evaluate(_BETA_LEFT)
    x, y = image["x"] + image["width"] / 2, image["y"] + image["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 20, y + 5)
    # 3px short of Beta's left edge: close enough to snap onto it.
    page.mouse.move(x + (target_left - image["x"]) + 3, y + 10)
    assert page.locator(".pw-guide-v").count() >= 1
    page.mouse.up()
    page.wait_for_selector(".pw-history-entry:has-text('Move/resize image')", timeout=10000)
    page.wait_for_function(
        f"() => Math.abs(document.querySelector('.pw-image-box').getBoundingClientRect().left - {target_left}) < 1.5",
        timeout=5000,
    )
    assert page.locator(".pw-guide").count() == 0


@pytest.mark.feature("EDT-14")
def test_arrow_keys_nudge_the_selection_as_one_move(page: Page, app_url: str, arrange_pdf: Path) -> None:
    _open_arrange(page, app_url, arrange_pdf)
    before = _shape_lefts(page)
    page.locator(".pw-shape-box").first.click()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Shift+ArrowRight")
    page.wait_for_selector(".pw-history-entry:has-text('Move 1 object')", timeout=10000)
    assert page.locator(".pw-history-entry").count() == 1
    assert (page.text_content(".pw-page-indicator") or "").startswith("1 /")  # arrows nudged, not paged
    scale = page.evaluate("() => document.querySelector('.pw-canvas-wrap canvas').getBoundingClientRect().width / 595")
    expected = before[0] + 12 * scale
    page.wait_for_function(
        "e => [...document.querySelectorAll('.pw-shape-box')]"
        ".some(b => Math.abs(b.getBoundingClientRect().left - e) < 2.5)",
        arg=expected,
        timeout=5000,
    )


@pytest.mark.feature("EDT-15")
def test_copy_paste_then_delete(page: Page, app_url: str, arrange_pdf: Path) -> None:
    _open_arrange(page, app_url, arrange_pdf)
    page.locator(".pw-shape-box").first.click()
    page.keyboard.press("Control+c")
    page.keyboard.press("Control+v")
    page.wait_for_selector(".pw-history-entry:has-text('Copy 1 object')", timeout=10000)
    page.wait_for_function("() => document.querySelectorAll('.pw-shape-box').length === 4", timeout=5000)
    page.keyboard.press("Delete")  # the pasted copy is the selection now
    page.wait_for_selector(".pw-history-entry:has-text('Delete 1 object')", timeout=10000)
    page.wait_for_function("() => document.querySelectorAll('.pw-shape-box').length === 3", timeout=5000)


@pytest.mark.feature("EDT-15")
def test_text_can_be_duplicated_and_deleted_from_the_keyboard(page: Page, app_url: str, arrange_pdf: Path) -> None:
    _open_arrange(page, app_url, arrange_pdf)
    count = "() => [...document.querySelectorAll('.pw-span-box')].filter(b => b.textContent === 'Beta note').length"
    page.click(".pw-span-box:has-text('Beta note')")
    page.keyboard.press("Escape")  # unchanged: hands the keyboard back to the page
    page.keyboard.press("Control+d")
    page.wait_for_selector(".pw-history-entry:has-text('Copy 1 object')", timeout=10000)
    page.wait_for_function(f"() => ({count})() === 2", timeout=5000)
    page.keyboard.press("Delete")
    page.wait_for_selector(".pw-history-entry:has-text('Delete 1 object')", timeout=10000)
    page.wait_for_function(f"() => ({count})() === 1", timeout=5000)
