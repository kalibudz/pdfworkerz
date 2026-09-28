"""Typed, serializable operations (COR-04). See engine.ops.base.

Importing this package registers every built-in Op, including the text
Ops in engine.ops.text (EDT-01..EDT-04, EDT-06, EDT-07, FNT-11) and the link Ops in
engine.ops.links (EDT-10) -- importing
engine.ops.base alone would not run their @register_op decorators.
"""

from __future__ import annotations

from engine.ops.base import InspectOp, Op, RenderPageOp, op_registry, parse_op
from engine.ops.links import AddLinkOp, PageLinksOp, RemoveLinkOp, UpdateLinkOp
from engine.ops.text import DeleteTextOp, InsertTextOp, ReflowTextOp, ReplaceTextOp, RestyleTextOp

__all__ = [
    "AddLinkOp",
    "DeleteTextOp",
    "InsertTextOp",
    "InspectOp",
    "Op",
    "PageLinksOp",
    "ReflowTextOp",
    "RemoveLinkOp",
    "RenderPageOp",
    "ReplaceTextOp",
    "RestyleTextOp",
    "UpdateLinkOp",
    "op_registry",
    "parse_op",
]
