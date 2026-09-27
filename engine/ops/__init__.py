"""Typed, serializable operations (COR-04). See engine.ops.base.

Importing this package registers every built-in Op, including the text
Ops in engine.ops.text (EDT-01..EDT-04, EDT-06) -- importing
engine.ops.base alone would not run their @register_op decorators.
"""

from __future__ import annotations

from engine.ops.base import InspectOp, Op, RenderPageOp, op_registry, parse_op
from engine.ops.text import DeleteTextOp, InsertTextOp, ReplaceTextOp, RestyleTextOp

__all__ = [
    "DeleteTextOp",
    "InsertTextOp",
    "InspectOp",
    "Op",
    "RenderPageOp",
    "ReplaceTextOp",
    "RestyleTextOp",
    "op_registry",
    "parse_op",
]
