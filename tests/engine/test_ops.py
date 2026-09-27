"""COR-04: the typed, serializable Op model."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import InspectOp, Op, RenderPageOp, json_schema_for_registry, op_registry, parse_op, register_op
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("COR-04")
def test_builtin_ops_are_registered() -> None:
    registry = op_registry()
    assert registry["inspect"] is InspectOp
    assert registry["render_page"] is RenderPageOp


@pytest.mark.feature("COR-04")
def test_op_serializes_and_round_trips_through_plain_dict() -> None:
    original = RenderPageOp(page_index=3, dpi=200)
    data = original.model_dump()
    assert data == {"op": "render_page", "page_index": 3, "dpi": 200}
    restored = parse_op(data)
    assert restored == original


@pytest.mark.feature("COR-04")
def test_op_serializes_to_and_from_json_text() -> None:
    original = InspectOp(password="secret")
    json_text = original.model_dump_json()
    restored = InspectOp.model_validate_json(json_text)
    assert restored == original


@pytest.mark.feature("COR-04")
def test_extra_fields_are_rejected_not_silently_ignored() -> None:
    with pytest.raises(ValidationError):
        RenderPageOp.model_validate({"op": "render_page", "page_index": 0, "not_a_real_field": 1})


@pytest.mark.feature("COR-04")
def test_parse_op_rejects_missing_op_field() -> None:
    with pytest.raises(OpValidationError, match="missing required field 'op'"):
        parse_op({"page_index": 0})


@pytest.mark.feature("COR-04")
def test_parse_op_rejects_unknown_op_name_and_lists_known_ones() -> None:
    with pytest.raises(OpValidationError, match="unknown op 'not_a_real_op'"):
        parse_op({"op": "not_a_real_op"})


@pytest.mark.feature("COR-04")
def test_register_op_rejects_a_duplicate_name_from_a_different_class() -> None:
    class _DupOp(Op):
        op: Literal["render_page"] = "render_page"

    with pytest.raises(OpValidationError, match="already registered"):
        register_op(_DupOp)


@pytest.mark.feature("COR-04")
def test_re_registering_the_same_class_is_a_no_op() -> None:
    register_op(RenderPageOp)  # must not raise
    assert op_registry()["render_page"] is RenderPageOp


@pytest.mark.feature("COR-04")
def test_json_schema_covers_every_registered_op() -> None:
    schema = json_schema_for_registry()
    assert schema["$schema"].startswith("https://json-schema.org/")
    titles = {branch.get("title") for branch in schema["oneOf"]}
    assert {"InspectOp", "RenderPageOp"} <= titles


@pytest.mark.feature("COR-04")
def test_inspect_op_applies_and_returns_the_real_inspection_report(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        report = InspectOp().apply(doc)
    assert report.page_count == 1


@pytest.mark.feature("COR-04")
def test_render_page_op_applies_and_returns_png_bytes(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        png = RenderPageOp(page_index=0, dpi=100).apply(doc)
    assert png.startswith(b"\x89PNG")


@pytest.mark.feature("COR-04")
def test_base_op_apply_is_not_implemented() -> None:
    class _BareOp(Op):
        pass

    with pytest.raises(NotImplementedError):
        _BareOp().apply(None)  # type: ignore[arg-type]
