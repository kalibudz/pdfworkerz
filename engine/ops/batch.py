"""CMD-01 / CMD-07: several Ops applied as one step.

A typed command like ``replace "a" with "b" on pages 1-3`` becomes one Op per page; wrapping
them in a BatchOp makes the whole command a single history entry and a single undo, and lets
a recipe say what command produced it. The journal rolls back the whole batch if any part fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import field_validator

from engine.errors import OpValidationError
from engine.ops.base import Op, parse_op, register_op

if TYPE_CHECKING:
    from engine.document import Document


@register_op
class BatchOp(Op):
    op: Literal["batch"] = "batch"
    command: str = ""
    """The command this batch was made from, for the history panel and recipes."""
    ops: list[dict[str, Any]]
    """The Ops to apply, in order, as plain dicts (each is validated as a real Op)."""

    @field_validator("ops")
    @classmethod
    def _each_is_a_real_op(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not value:
            raise ValueError("a batch needs at least one op")
        for item in value:
            try:
                parse_op(item)
            except OpValidationError as exc:
                raise ValueError(str(exc)) from exc
        return value

    def parsed(self) -> list[Op]:
        return [parse_op(item) for item in self.ops]

    def check_pages(self, document: Document) -> None:
        for op in self.parsed():
            op.check_pages(document)

    def apply(self, document: Document) -> list[Any]:
        return [op.apply(document) for op in self.parsed()]
