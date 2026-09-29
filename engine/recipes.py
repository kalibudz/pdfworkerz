"""CMD-07: recipes -- an editing session saved as an ordered list of Ops, and replayed.

A recipe is YAML or JSON (YAML is a superset, so one loader reads both)::

    recipe: bump-revision
    ops:
      - {op: replace_text, match: "Rev C", replacement: "Rev D"}
      - {op: restyle_text, match: "Rev D", bold: true}

Every entry is a real Op, exactly as docs/ops.schema.json describes it -- the same objects a
click or a typed command produces -- so a recipe can't express anything the editor can't do,
and a mistake is reported with its position instead of being skipped. (SPEC.md section 9
sketches a richer "target"/"style" shape; recipes use the Op schema that actually exists.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import yaml

from engine import __version__
from engine.errors import OpValidationError, PdfWorkerzError
from engine.ops.base import Op, parse_op


class RecipeError(PdfWorkerzError):
    """A recipe that can't be read, or one of whose steps isn't a valid Op."""


@dataclass
class Recipe:
    name: str
    ops: list[dict[str, Any]]
    """Validated Ops as plain dicts, in order."""


def load_recipe(text: str) -> Recipe:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RecipeError(f"the recipe isn't valid YAML or JSON: {exc}") from exc
    if isinstance(data, list):
        name, steps = "recipe", data
    elif isinstance(data, dict) and isinstance(data.get("ops"), list):
        name, steps = str(data.get("recipe") or "recipe"), data["ops"]
    else:
        raise RecipeError('a recipe needs an "ops" list (or is itself a list of ops)')
    if not steps:
        raise RecipeError("the recipe has no ops")
    ops = []
    for number, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise RecipeError(f"step {number} isn't an op (expected a mapping with an 'op' field)")
        try:
            ops.append(parse_op(step).model_dump(mode="json"))
        except OpValidationError as exc:
            raise RecipeError(f"step {number}: {exc}") from exc
    return Recipe(name, ops)


def dump_recipe(ops: list[Op], *, name: str = "recipe", fmt: str = "yaml") -> str:
    """The Ops as a recipe, leaving out fields that are at their default so it stays readable."""
    steps = [{"op": op.model_dump()["op"], **op.model_dump(mode="json", exclude_defaults=True)} for op in ops]
    document = {"recipe": name, "pdfworkerz": __version__, "ops": steps}
    if fmt == "json":
        return json.dumps(document, indent=2) + "\n"
    if fmt != "yaml":
        raise RecipeError(f"unknown recipe format {fmt!r}: use yaml or json")
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def as_single_op(recipe: Recipe) -> dict[str, Any]:
    """The whole recipe as one batch Op: one history entry, one undo, all-or-nothing."""
    return {"op": "batch", "command": f"recipe {recipe.name}", "ops": recipe.ops}
