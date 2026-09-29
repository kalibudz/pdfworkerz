"""The Op model (COR-04): every action PDFWorkerz can take, typed and serializable.

Clicks in the UI, commands typed in the command bar, CLI flags and recipe
steps all produce the same Op objects (SPEC.md section 9 / architecture
rule 1). An Op:

- declares ``op: Literal["name"]`` as its discriminator,
- is registered with :func:`register_op` under that name,
- validates strictly (``extra="forbid"``): a typo in a field name fails
  loudly instead of being silently ignored,
- knows how to run itself against an open :class:`~engine.document.Document`
  through :meth:`Op.apply`.

Concrete edit operations (replace text, redact, merge, ...) are added
feature by feature from phase P2 onward, each in its own module, each
imported here (or by the caller) so :func:`register_op` runs and the type
is added to the registry. Two operations already exist in P1, built on
capabilities that already exist: :class:`InspectOp` and
:class:`RenderPageOp`.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.errors import OpValidationError
from engine.fonts.style import SpanTrace, extract_page_spans

if TYPE_CHECKING:
    from engine.document import Document
    from engine.inspect import InspectionReport

_REGISTRY: dict[str, type[Op]] = {}
MIN_RENDER_DPI = 18
MAX_RENDER_DPI = 600  # a US-letter page at 600 dpi is already ~34 megapixels


class Op(BaseModel):
    """Base class for every typed operation. Do not instantiate directly.

    Every concrete subclass declares its own ``op: Literal["name"] = "name"``
    discriminator field; the base class deliberately declares none, so it
    can never be registered or mistaken for a real operation.
    """

    model_config = ConfigDict(extra="forbid")

    def apply(self, document: Document) -> Any:
        """Run this operation against an open document and return its result."""
        raise NotImplementedError(f"{type(self).__name__} does not implement apply()")

    def check_pages(self, document: Document) -> None:
        """Refuse any ``*page_index`` field outside the document, before anything runs:
        an out-of-range page otherwise surfaced as pymupdf's raw IndexError."""
        for name in type(self).model_fields:
            value = getattr(self, name)
            values = value if name.endswith("page_indices") and isinstance(value, list) else [value]
            if not (name.endswith("page_index") or name.endswith("page_indices")):
                continue
            for item in values:
                if isinstance(item, int) and not 0 <= item < document.page_count:
                    raise OpValidationError(
                        f"{name} {item} is out of range: the document has {document.page_count} page(s) (0-based)"
                    )


def register_op(cls: type[Op]) -> type[Op]:
    """Class decorator: add a concrete Op to the registry under its ``op`` field default."""
    name = cls.model_fields["op"].default
    if not isinstance(name, str) or not name:
        raise OpValidationError(f"{cls.__name__}.op must have a non-empty string default")
    if name in _REGISTRY and _REGISTRY[name] is not cls:
        raise OpValidationError(f"an Op named {name!r} is already registered as {_REGISTRY[name].__name__}")
    _REGISTRY[name] = cls
    return cls


def op_registry() -> MappingProxyType[str, type[Op]]:
    """A read-only view of every registered Op type, keyed by its ``op`` name."""
    return MappingProxyType(_REGISTRY)


def parse_op(data: dict[str, Any]) -> Op:
    """Validate a plain dict (as loaded from JSON or YAML) into its typed Op.

    Raises :class:`OpValidationError` if ``op`` is missing or names a type
    that isn't registered, or if the model itself fails validation -- never
    guesses at what the caller meant (SPEC.md section 8.3: the tool never
    guesses silently).
    """
    name = data.get("op")
    if name is None:
        raise OpValidationError("missing required field 'op'")
    cls = _REGISTRY.get(name)
    if cls is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise OpValidationError(f"unknown op {name!r}; known ops: {known}")
    try:
        return cls.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError, re-raised as our own error type
        raise OpValidationError(f"{name}: {exc}") from exc


def json_schema_for_registry() -> dict[str, Any]:
    """A combined JSON schema covering every registered Op (docs/ops.schema.json)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "PDFWorkerz Op",
        "oneOf": [cls.model_json_schema() for cls in _REGISTRY.values()],
    }


@register_op
class InspectOp(Op):
    """Produce the COR-03 inspection report for the target document."""

    op: Literal["inspect"] = "inspect"
    password: str = ""

    def apply(self, document: Document) -> InspectionReport:
        return document.inspect(password=self.password)


@register_op
class RenderPageOp(Op):
    """Render one page to PNG bytes (COR-02)."""

    op: Literal["render_page"] = "render_page"
    page_index: int = 0
    dpi: int = Field(default=150, ge=MIN_RENDER_DPI, le=MAX_RENDER_DPI)

    def apply(self, document: Document) -> bytes:
        return document.render_page(self.page_index, dpi=self.dpi)


@register_op
class PageSpansOp(Op):
    """Every text span on one page, with its style (FNT-01) and text state
    (FNT-02) -- what UI-02's click-to-edit overlay and UI-03's inspector
    panel are both built on (state/checkpoint.json's own plan for them).
    A read-only query, like InspectOp/RenderPageOp: never journaled, so a
    caller applies it directly rather than through the undo/redo journal
    (see server/app.py's dedicated GET route for this one, matching
    render_page's own convenience route rather than the generic Op
    endpoint)."""

    op: Literal["page_spans"] = "page_spans"
    page_index: int = 0

    def apply(self, document: Document) -> list[SpanTrace]:
        return extract_page_spans(document.raw, self.page_index)
