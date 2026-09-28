"""EDT-10: add, edit and remove hyperlinks."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import parse_op
from engine.ops.links import AddLinkOp, PageLinksOp, RemoveLinkOp, UpdateLinkOp
from tests.corpus.build_corpus import Corpus


@pytest.fixture
def doc(corpus: Corpus, work_dir: Path) -> Document:
    path = work_dir / "multi.pdf"
    shutil.copy(corpus.multi_page, path)
    return Document.open(path)


def _reopened(doc: Document) -> Document:
    """Links must survive a save, not just live in PyMuPDF's in-memory page."""
    return Document.from_bytes(doc.to_bytes())


@pytest.mark.feature("EDT-10")
def test_add_a_uri_link_and_read_it_back_after_a_save(doc: Document) -> None:
    added = AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri="https://example.com/a").apply(doc)
    assert added.kind == "uri" and added.index == 0

    links = PageLinksOp(page_index=0).apply(_reopened(doc))
    assert len(links) == 1
    assert links[0].uri == "https://example.com/a"
    assert links[0].rect == pytest.approx((72, 72, 200, 90))


@pytest.mark.feature("EDT-10")
def test_add_an_internal_go_to_page_link(doc: Document) -> None:
    AddLinkOp(page_index=0, rect=(72, 100, 200, 120), target_page=2).apply(doc)
    link = PageLinksOp(page_index=0).apply(_reopened(doc))[0]
    assert link.kind == "goto"
    assert link.target_page == 2


@pytest.mark.feature("EDT-10")
def test_update_moves_and_retargets_a_link(doc: Document) -> None:
    AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri="https://example.com/a").apply(doc)
    UpdateLinkOp(page_index=0, index=0, rect=(80, 300, 180, 320), uri="https://example.com/b").apply(doc)

    link = PageLinksOp(page_index=0).apply(_reopened(doc))[0]
    assert link.uri == "https://example.com/b"
    assert link.rect == pytest.approx((80, 300, 180, 320))


@pytest.mark.feature("EDT-10")
def test_update_can_switch_a_uri_link_to_an_internal_link(doc: Document) -> None:
    AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri="https://example.com/a").apply(doc)
    UpdateLinkOp(page_index=0, index=0, target_page=1).apply(doc)

    link = PageLinksOp(page_index=0).apply(_reopened(doc))[0]
    assert (link.kind, link.target_page, link.uri) == ("goto", 1, None)


@pytest.mark.feature("EDT-10")
def test_remove_deletes_only_the_indexed_link(doc: Document) -> None:
    AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri="https://example.com/keep").apply(doc)
    AddLinkOp(page_index=0, rect=(72, 120, 200, 140), uri="https://example.com/drop").apply(doc)

    removed = RemoveLinkOp(page_index=0, index=1).apply(doc)
    assert removed.uri == "https://example.com/drop"
    assert [link.uri for link in PageLinksOp(page_index=0).apply(_reopened(doc))] == ["https://example.com/keep"]


@pytest.mark.feature("EDT-10")
@pytest.mark.parametrize("uri", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "example.com"])
def test_unsafe_or_schemeless_uris_are_refused(doc: Document, uri: str) -> None:
    with pytest.raises(OpValidationError, match="scheme"):
        AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri=uri).apply(doc)
    assert PageLinksOp(page_index=0).apply(doc) == []


@pytest.mark.feature("EDT-10")
def test_mailto_links_are_allowed(doc: Document) -> None:
    AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri="mailto:someone@example.com").apply(doc)
    assert PageLinksOp(page_index=0).apply(doc)[0].uri == "mailto:someone@example.com"


@pytest.mark.feature("EDT-10")
@pytest.mark.parametrize(
    ("op", "message"),
    [
        (AddLinkOp(page_index=0, rect=(72, 72, 200, 90)), "exactly one"),
        (AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri="https://x.example", target_page=1), "exactly one"),
        (AddLinkOp(page_index=0, rect=(72, 72, 200, 90), target_page=99), "out of range"),
        (AddLinkOp(page_index=0, rect=(200, 90, 72, 72), uri="https://x.example"), "empty"),
        (AddLinkOp(page_index=0, rect=(5000, 5000, 5100, 5100), uri="https://x.example"), "outside the page"),
        (AddLinkOp(page_index=99, rect=(72, 72, 200, 90), uri="https://x.example"), "out of range"),
        (UpdateLinkOp(page_index=0, index=0, uri="https://x.example"), "out of range"),
        (RemoveLinkOp(page_index=0, index=0), "out of range"),
    ],
)
def test_invalid_link_ops_are_rejected(doc: Document, op: AddLinkOp, message: str) -> None:
    with pytest.raises(OpValidationError, match=message):
        op.apply(doc)


@pytest.mark.feature("EDT-10")
def test_update_with_nothing_to_change_is_rejected(doc: Document) -> None:
    AddLinkOp(page_index=0, rect=(72, 72, 200, 90), uri="https://example.com/a").apply(doc)
    with pytest.raises(OpValidationError, match="nothing to change"):
        UpdateLinkOp(page_index=0, index=0).apply(doc)


@pytest.mark.feature("EDT-10")
def test_link_ops_round_trip_through_json() -> None:
    for op in (
        AddLinkOp(page_index=0, rect=(1, 2, 3, 4), uri="https://example.com"),
        UpdateLinkOp(page_index=1, index=2, target_page=0),
        RemoveLinkOp(page_index=0, index=0),
        PageLinksOp(page_index=3),
    ):
        assert parse_op(op.model_dump()) == op
