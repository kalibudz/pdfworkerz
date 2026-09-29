"""Typed, serializable operations (COR-04). See engine.ops.base.

Importing this package registers every built-in Op, including the text
Ops in engine.ops.text (EDT-01..EDT-04, EDT-06, EDT-07, FNT-11) and the link Ops in
engine.ops.links (EDT-10) and the image Ops in engine.ops.images (EDT-08) and
the shape Ops in engine.ops.shapes (EDT-09) and the spell-check Ops
in engine.ops.spellcheck (EDT-11) -- importing
engine.ops.base alone would not run their @register_op decorators.
"""

from __future__ import annotations

from engine.ops.base import InspectOp, Op, RenderPageOp, op_registry, parse_op
from engine.ops.images import (
    CropImageOp,
    DeleteImageOp,
    InsertImageOp,
    MoveImageOp,
    PageImagesOp,
    ReplaceImageOp,
)
from engine.ops.links import AddLinkOp, PageLinksOp, RemoveLinkOp, UpdateLinkOp
from engine.ops.shapes import DeleteShapeOp, DrawShapeOp, EditShapeOp, PageShapesOp
from engine.ops.spellcheck import CorrectWordOp, SpellCheckOp
from engine.ops.text import DeleteTextOp, InsertTextOp, ReflowTextOp, ReplaceTextOp, RestyleSpanOp, RestyleTextOp

__all__ = [
    "AddLinkOp",
    "CorrectWordOp",
    "CropImageOp",
    "DeleteImageOp",
    "DeleteShapeOp",
    "DeleteTextOp",
    "DrawShapeOp",
    "EditShapeOp",
    "InsertImageOp",
    "InsertTextOp",
    "InspectOp",
    "MoveImageOp",
    "Op",
    "PageImagesOp",
    "PageLinksOp",
    "PageShapesOp",
    "ReflowTextOp",
    "RemoveLinkOp",
    "RenderPageOp",
    "ReplaceImageOp",
    "ReplaceTextOp",
    "RestyleSpanOp",
    "RestyleTextOp",
    "SpellCheckOp",
    "UpdateLinkOp",
    "op_registry",
    "parse_op",
]
