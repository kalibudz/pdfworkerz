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
from engine.ops.batch import BatchOp
from engine.ops.design import BackgroundOp, BatesOp, HeaderFooterOp, PageNumbersOp, StampOp, WatermarkOp
from engine.ops.images import (
    CropImageOp,
    DeleteImageOp,
    InsertImageOp,
    MoveImageOp,
    PageImagesOp,
    ReplaceImageOp,
)
from engine.ops.layout import BookletOp, CropPagesOp, NUpOp, ResizePagesOp, UncropPagesOp
from engine.ops.links import AddLinkOp, PageLinksOp, RemoveLinkOp, UpdateLinkOp
from engine.ops.pages import (
    DeletePagesOp,
    DuplicatePagesOp,
    ExtractPagesOp,
    FindBlankPagesOp,
    InsertPagesOp,
    MergeOp,
    MovePagesOp,
    RemoveBlankPagesOp,
    ReorderPagesOp,
    RotatePagesOp,
    SplitOp,
)
from engine.ops.shapes import DeleteShapeOp, DrawShapeOp, EditShapeOp, PageShapesOp
from engine.ops.spellcheck import CorrectWordOp, SpellCheckOp
from engine.ops.text import DeleteTextOp, InsertTextOp, ReflowTextOp, ReplaceTextOp, RestyleSpanOp, RestyleTextOp

__all__ = [
    "AddLinkOp",
    "BackgroundOp",
    "BatchOp",
    "BatesOp",
    "BookletOp",
    "CorrectWordOp",
    "CropImageOp",
    "CropPagesOp",
    "DeleteImageOp",
    "DeletePagesOp",
    "DeleteShapeOp",
    "DeleteTextOp",
    "DrawShapeOp",
    "DuplicatePagesOp",
    "EditShapeOp",
    "ExtractPagesOp",
    "FindBlankPagesOp",
    "HeaderFooterOp",
    "InsertImageOp",
    "InsertPagesOp",
    "InsertTextOp",
    "InspectOp",
    "MergeOp",
    "MoveImageOp",
    "MovePagesOp",
    "NUpOp",
    "Op",
    "PageImagesOp",
    "PageLinksOp",
    "PageNumbersOp",
    "PageShapesOp",
    "ReflowTextOp",
    "RemoveBlankPagesOp",
    "RemoveLinkOp",
    "RenderPageOp",
    "ReorderPagesOp",
    "ReplaceImageOp",
    "ReplaceTextOp",
    "ResizePagesOp",
    "RestyleSpanOp",
    "RestyleTextOp",
    "RotatePagesOp",
    "SpellCheckOp",
    "SplitOp",
    "StampOp",
    "UncropPagesOp",
    "UpdateLinkOp",
    "WatermarkOp",
    "op_registry",
    "parse_op",
]
