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
