"""UI-01 (page canvas with thumbnail rail) and UI-06 (password prompt),
proven against a real, built frontend in a real browser -- not a DOM/unit
test double. See conftest.py for how the API server, the static build and
the browser are wired together.
"""

from __future__ import annotations

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
    page.wait_for_selector(".pw-span-box", timeout=5000)


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
    page.click(".pw-toolbar button[title='Zoom in']")
    page.wait_for_function(f"() => document.querySelector('.pw-page-area canvas')?.width > {before}", timeout=5000)


# -- UI-02 (click-to-edit overlay) and UI-03 (inspector panel) --

_EXACT_DOT = "() => document.querySelector('.pw-match-dot')?.classList.contains('pw-match-exact')"
_FALLBACK_DOT = "() => document.querySelector('.pw-match-dot')?.classList.contains('pw-match-fallback')"
_STILL_EDITING = "() => !!document.querySelector('.pw-span-box.pw-span-editing')"
_NOT_EDITING = "() => !document.querySelector('.pw-span-box.pw-span-editing')"


def _open_and_click_first_span(page: Page, app_url: str, path: str) -> None:
    page.goto(app_url)
    page.wait_for_selector("#pw-open-path", timeout=5000)
    _open_path(page, path)
    _wait_overlay_ready(page)
    page.click(".pw-span-box")
    page.wait_for_selector(".pw-span-box.pw-span-editing", timeout=3000)


@pytest.mark.feature("UI-02")
def test_clicking_a_span_opens_an_editable_overlay(page: Page, app_url: str, corpus: Corpus) -> None:
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    is_editable = page.eval_on_selector(".pw-span-box.pw-span-editing", "el => el.isContentEditable")
    assert is_editable is True


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
def test_escape_discards_the_edit(page: Page, app_url: str, corpus: Corpus) -> None:
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    original = page.eval_on_selector(".pw-span-box", "el => el.textContent")
    page.keyboard.type("Should Not Be Saved")
    page.keyboard.press("Escape")
    page.wait_for_function(_NOT_EDITING, timeout=3000)
    assert page.eval_on_selector(".pw-span-box", "el => el.textContent") == original


@pytest.mark.feature("UI-02")
def test_clicking_away_discards_the_edit(page: Page, app_url: str, corpus: Corpus) -> None:
    """A real bug, caught this way and not by inspection: toggling
    contentEditable off can itself fire a synchronous blur, re-entering the
    cancel handler before the outer call finished and crashing on a null
    box reference. Clicking away is exactly the interaction that exercises
    that path."""
    _open_and_click_first_span(page, app_url, str(corpus.simple))
    original = page.eval_on_selector(".pw-span-box", "el => el.textContent")
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.keyboard.type("Should Not Be Saved Either")
    page.click(".pw-page-area", position={"x": 5, "y": 5})
    page.wait_for_function(_NOT_EDITING, timeout=3000)
    assert page.eval_on_selector(".pw-span-box", "el => el.textContent") == original
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
    page.wait_for_function(_NOT_EDITING, timeout=5000)
    assert dialogs == []
    assert page.eval_on_selector(".pw-span-box", "el => el.textContent") == "Hello, Editor."


@pytest.mark.feature("UI-02")
def test_a_fallback_match_asks_for_confirmation_before_committing(page: Page, app_url: str, corpus: Corpus) -> None:
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    _open_and_click_first_span(page, app_url, str(corpus.type3))
    page.wait_for_function(_FALLBACK_DOT, timeout=3000)
    page.keyboard.type("X")
    page.wait_for_timeout(400)
    page.keyboard.press("Enter")
    page.wait_for_function(_NOT_EDITING, timeout=5000)
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
    assert page.eval_on_selector_all(".pw-span-box.pw-span-editing", "els => els.length") == 1
    assert page.eval_on_selector(".pw-span-box.pw-span-editing", "el => el.textContent") == "X"


# -- UI-04 (history panel with undo/redo) --


def _commit_edit(page: Page, app_url: str, path: str, new_text: str) -> None:
    _open_and_click_first_span(page, app_url, path)
    page.wait_for_function(_EXACT_DOT, timeout=3000)
    page.keyboard.type(new_text)
    page.wait_for_timeout(400)  # let the debounced preview resolve before committing
    page.keyboard.press("Enter")
    page.wait_for_function(_NOT_EDITING, timeout=5000)


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
    page.wait_for_selector(".pw-history-entry", timeout=5000)
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
