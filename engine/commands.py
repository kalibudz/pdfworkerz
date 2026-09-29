"""The command bar's language (CMD-01..CMD-06, SPEC.md section 8.3): plain-English commands
parsed by a fixed Lark grammar into the same Ops a click or a recipe produces.

Nothing is guessed. A command either parses exactly -- and becomes Ops the caller can
preview (:func:`preview_plan`) before applying -- or it fails with a :class:`CommandError`
that says why, lists the closest valid forms ("did you mean ...", rapidfuzz) and gives a
syntax hint. A misspelled command is never run as its nearest guess.

Supported now (the actions whose Ops exist)::

    replace "2024" with "2025" on all pages            replace /Rev\\s+[A-C]/ with "Rev D" on pages 1-3
    delete "DRAFT" on odd pages                        insert "Checked by: K.H." below "Prepared by"
    set size 11 bold for "Note:" on page 3             set font "Times" color #cc0000 for /Total.*/
    undo                                               redo

Page numbers are 1-based, as a person reads them; Ops use 0-based page indices.
Actions from later phases (merge, split, watermark, ...) are recognized and refused with
the phase they are planned for, rather than being "corrected" into something else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lark import Lark, Token, Transformer, Tree, UnexpectedInput, v_args
from lark.exceptions import VisitError
from rapidfuzz import fuzz, process

from engine.errors import PdfWorkerzError

GRAMMAR_TEMPLATE = r"""
start: replace | delete | insert | set | undo | redo
     | delete_pages | rotate_pages | move_pages | duplicate_pages | insert_blank
     | extract_pages | split_doc | merge_doc | remove_blank

replace: REPLACE target WITH STRING (option | scope)*
delete: DELETE target (option | scope)*
insert: INSERT STRING placement (style | scope)*
set: SET style+ FOR target (option | scope)*
undo: UNDO
redo: REDO

delete_pages: DELETE pageset
rotate_pages: ROTATE pageset (BY number)? (CLOCKWISE | COUNTERCLOCKWISE)?
move_pages: MOVE pageset RELATION PAGE NUMBER   -> move_relative
          | MOVE pageset TO THE? (START | END)   -> move_edge
duplicate_pages: DUPLICATE pageset (number TIMES)?
insert_blank: INSERT number BLANK (PAGE | PAGES) RELATION PAGE NUMBER
extract_pages: EXTRACT pageset TO STRING
split_doc: SPLIT EVERY number PAGES INTO STRING   -> split_every
         | SPLIT AT BOOKMARKS INTO STRING         -> split_bookmarks
merge_doc: MERGE STRING (RELATION PAGE NUMBER)?
remove_blank: REMOVE BLANK PAGES

pageset: PAGE NUMBER            -> ps_one
       | PAGES ranges           -> ps_ranges
       | ALL PAGES              -> ps_all
       | ODD PAGES              -> ps_odd
       | EVEN PAGES             -> ps_even

target: STRING   -> literal
      | REGEX    -> regex
      | TEXT     -> all_text

placement: RELATION STRING      -> anchored
         | AT number ","? number -> at_point

scope: ON PAGE NUMBER          -> one_page
     | ON PAGES ranges         -> page_ranges
     | ON ALL PAGES            -> all_pages
     | ON ODD PAGES            -> odd_pages
     | ON EVEN PAGES           -> even_pages

ranges: span ("," span)*
span: NUMBER ("-" NUMBER)?
number: NUMBER

option: WHOLE WORD             -> whole_word
      | IGNORING CASE          -> ignore_case
      | FIT                    -> fit

style: MATCH STYLE             -> match_style
     | BOLD                    -> bold
     | NOT BOLD                -> not_bold
     | ITALIC                  -> italic
     | NOT ITALIC              -> not_italic
     | SIZE number             -> size
     | COLOR (HEXCOLOR | COLORNAME) -> color
     | FONT STRING             -> font

{keywords}
HEXCOLOR: /#[0-9a-fA-F]{6}/
REGEX: /\/(\\\/|[^\/\n])+\/i?/
%import common.ESCAPED_STRING -> STRING
NUMBER: /[0-9]+(\.[0-9]+)?/

%ignore /[ \t]+/
"""

# Keywords match whole words only (a trailing word boundary): with plain string terminals the lexer read
# "Redo" as the color "red" followed by "o".
KEYWORDS: dict[str, tuple[str, ...]] = {
    "REPLACE": ("replace",),
    "DELETE": ("delete",),
    "INSERT": ("insert",),
    "SET": ("set",),
    "UNDO": ("undo",),
    "REDO": ("redo",),
    "WITH": ("with",),
    "FOR": ("for",),
    "TEXT": ("text",),
    "RELATION": ("below", "above", "after", "before"),
    "AT": ("at",),
    "ON": ("on",),
    "PAGES": ("pages",),
    "PAGE": ("page",),
    "ALL": ("all",),
    "ODD": ("odd",),
    "EVEN": ("even",),
    "WHOLE": ("whole",),
    "WORD": ("word",),
    "IGNORING": ("ignoring",),
    "CASE": ("case",),
    "FIT": ("fit",),
    "MATCH": ("match",),
    "STYLE": ("style",),
    "BOLD": ("bold",),
    "NOT": ("not",),
    "ITALIC": ("italic",),
    "SIZE": ("size",),
    "COLOR": ("color", "colour"),
    "FONT": ("font",),
    "COLORNAME": ("black", "white", "red", "green", "blue", "gray", "grey"),
    "ROTATE": ("rotate",),
    "BY": ("by",),
    "COUNTERCLOCKWISE": ("counterclockwise", "anticlockwise"),
    "CLOCKWISE": ("clockwise",),
    "MOVE": ("move",),
    "TO": ("to",),
    "THE": ("the",),
    "START": ("start", "beginning", "front"),
    "END": ("end",),
    "DUPLICATE": ("duplicate",),
    "TIMES": ("times",),
    "BLANK": ("blank",),
    "EXTRACT": ("extract",),
    "SPLIT": ("split",),
    "EVERY": ("every",),
    "INTO": ("into",),
    "BOOKMARKS": ("bookmarks",),
    "MERGE": ("merge",),
    "REMOVE": ("remove",),
}
GRAMMAR = GRAMMAR_TEMPLATE.replace(
    "{keywords}",
    "\n".join(f"{name}: /(?:{'|'.join(words)})\\b/i" for name, words in KEYWORDS.items()),
)
_PARSER = Lark(GRAMMAR, parser="lalr", lexer="basic", maybe_placeholders=False)

VERBS = [
    "replace",
    "delete",
    "insert",
    "set",
    "undo",
    "redo",
    "rotate",
    "move",
    "duplicate",
    "extract",
    "split",
    "merge",
    "remove",
]
EXAMPLES = [
    'replace "old" with "new" on all pages',
    'replace /Rev\\s+[A-C]/ with "Rev D" on pages 1-3',
    'delete "DRAFT" on odd pages',
    'insert "Checked by" below "Prepared by"',
    'insert "Note" at 72, 700 font "Helvetica" size 12',
    'set size 11 bold for "Note:" on page 3',
    'set font "Times" color #cc0000 for "Total"',
    "undo",
    "redo",
    "delete pages 7, 9-10",
    "move pages 5-6 after page 1",
    "rotate pages 2-3 by 90",
    "duplicate page 1 2 times",
    "insert 1 blank page after page 3",
    'extract pages 1-2 to "part.pdf"',
    'split every 10 pages into "parts"',
    'merge "appendix.pdf" after page 4',
    "remove blank pages",
]
# SPEC.md section 8.3's other actions, refused with the phase that brings them.
LATER_ACTIONS = {
    **dict.fromkeys(["crop", "resize", "impose"], "P5 (page geometry, next in this phase)"),
    **dict.fromkeys(["number", "bates", "watermark", "stamp", "header", "footer", "bookmark"], "P5 (page design)"),
    **dict.fromkeys(["redact", "protect", "unlock", "sign", "fill", "flatten"], "P6 (forms, signatures, security)"),
    **dict.fromkeys(["ocr", "convert"], "P7 (OCR and conversions)"),
    **dict.fromkeys(["compress", "compare"], "P8 (optimize and compare)"),
}
SYNTAX_HINT = (
    "Text commands: replace, delete, insert, set; page commands: delete pages, rotate, move, duplicate, "
    "insert N blank pages, extract, split, merge, remove blank pages; and undo, redo. "
    'Put text in "double quotes", '
    "a regular expression in /slashes/, and pages as: on page 3 | on pages 1-3,5 | on all pages | on odd pages."
)
_NAMED_COLORS = {
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "red": (0.8, 0.0, 0.0),
    "green": (0.0, 0.5, 0.0),
    "blue": (0.0, 0.2, 0.8),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
}
_TOKEN_TEXT = {
    "STRING": '"text"',
    "REGEX": "/regex/",
    "NUMBER": "12",
    "HEXCOLOR": "#cc0000",
    "COLORNAME": "red",
    "RELATION": "below",
    "COMMA": ",",
    "MINUS": "-",
}


class CommandError(PdfWorkerzError):
    """A command that doesn't parse, or asks for something that can't be done. Carries the
    closest valid commands and a syntax hint for the command bar to show."""

    def __init__(self, message: str, suggestions: list[str] | None = None, hint: str = SYNTAX_HINT) -> None:
        super().__init__(message)
        self.suggestions = suggestions or []
        self.hint = hint


@dataclass
class CommandPlan:
    """What a command will do: the Ops to apply (one, or several wrapped in a batch), or an
    undo/redo; plus a sentence describing it."""

    text: str
    description: str
    ops: list[dict[str, Any]] = field(default_factory=list)
    special: str | None = None
    """"undo" or "redo" -- journal actions, not Ops."""

    def op(self) -> dict[str, Any]:
        """The single Op to record: the only one, or all of them as one batch."""
        if len(self.ops) == 1:
            return self.ops[0]
        return {"op": "batch", "command": self.text, "ops": self.ops}


def _page_number(token: Token) -> int:
    if "." in str(token):
        raise CommandError(f"page numbers are whole numbers, not {token}")
    return int(token)


def _unquote(token: Token) -> str:
    """The text inside a quoted string, with its escapes resolved: a backslash before a quote
    or before another backslash stands for that character."""
    backslash = chr(92)
    inner, out, i = str(token)[1:-1], [], 0
    while i < len(inner):
        if inner[i] == backslash and i + 1 < len(inner) and inner[i + 1] in ('"', backslash):
            out.append(inner[i + 1])
            i += 2
            continue
        out.append(inner[i])
        i += 1
    return "".join(out)


@v_args(inline=True)
class _Build(Transformer[Token, Any]):
    # Leaves: each rule becomes a small (kind, value) pair the command builders read.
    def literal(self, token: Token) -> tuple[str, Any]:
        text = _unquote(token)
        if not text:
            raise CommandError('the text to find is empty: put something between the quotes, e.g. "Total"')
        return ("target", {"match": text, "mode": "literal", "label": str(token)})

    def regex(self, token: Token) -> tuple[str, Any]:
        raw = str(token)
        ignore_case = raw.endswith("/i")
        body = raw[1:-2] if ignore_case else raw[1:-1]
        body = body.replace("\\/", "/")
        try:
            re.compile(body)
        except re.error as exc:
            raise CommandError(f"the regular expression {raw} is not valid: {exc}") from exc
        return ("target", {"match": body, "mode": "regex", "ignore_case": ignore_case, "label": raw})

    def all_text(self, _token: Token) -> tuple[str, Any]:
        return ("target", {"match": ".+", "mode": "regex", "label": "all text"})

    def anchored(self, relation: Token, anchor: Token) -> tuple[str, Any]:
        return ("placement", {"anchor": str(relation).lower(), "reference_match": _unquote(anchor)})

    def at_point(self, _at: Token, x: float, *rest: Any) -> tuple[str, Any]:
        return ("placement", {"position": (x, float(rest[-1]))})

    def number(self, token: Token) -> float:
        return float(token)

    def ps_one(self, _page: Token, number: Token) -> tuple[str, Any]:
        return ("pageset", ("pages", [_page_number(number)]))

    def ps_ranges(self, _pages: Token, pages: list[int]) -> tuple[str, Any]:
        return ("pageset", ("pages", pages))

    def ps_all(self, *_tokens: Token) -> tuple[str, Any]:
        return ("pageset", ("all", None))

    def ps_odd(self, *_tokens: Token) -> tuple[str, Any]:
        return ("pageset", ("odd", None))

    def ps_even(self, *_tokens: Token) -> tuple[str, Any]:
        return ("pageset", ("even", None))

    def one_page(self, _on: Token, _page: Token, number: Token) -> tuple[str, Any]:
        return ("scope", ("pages", [_page_number(number)]))

    def page_ranges(self, _on: Token, _pages: Token, pages: list[int]) -> tuple[str, Any]:
        return ("scope", ("pages", pages))

    def all_pages(self, *_tokens: Token) -> tuple[str, Any]:
        return ("scope", ("all", None))

    def odd_pages(self, *_tokens: Token) -> tuple[str, Any]:
        return ("scope", ("odd", None))

    def even_pages(self, *_tokens: Token) -> tuple[str, Any]:
        return ("scope", ("even", None))

    def ranges(self, *spans: list[int]) -> list[int]:
        return [page for span in spans for page in span]

    def span(self, first: Token, last: Token | None = None) -> list[int]:
        start = _page_number(first)
        end = _page_number(last) if last is not None else start
        if end < start:
            raise CommandError(f"the page range {start}-{end} runs backwards")
        return list(range(start, end + 1))

    def whole_word(self, *_tokens: Token) -> tuple[str, Any]:
        return ("option", ("whole_word", True))

    def ignore_case(self, *_tokens: Token) -> tuple[str, Any]:
        return ("option", ("case_sensitive", False))

    def fit(self, *_tokens: Token) -> tuple[str, Any]:
        return ("option", ("fit", True))

    def match_style(self, *_tokens: Token) -> tuple[str, Any]:
        return ("style", ("match_style", True))

    def bold(self, *_tokens: Token) -> tuple[str, Any]:
        return ("style", ("bold", True))

    def not_bold(self, *_tokens: Token) -> tuple[str, Any]:
        return ("style", ("bold", False))

    def italic(self, *_tokens: Token) -> tuple[str, Any]:
        return ("style", ("italic", True))

    def not_italic(self, *_tokens: Token) -> tuple[str, Any]:
        return ("style", ("italic", False))

    def size(self, _size: Token, value: float) -> tuple[str, Any]:
        points = value
        if not 1 <= points <= 400:
            raise CommandError(f"size {value:g} is out of range (1-400 points)")
        return ("style", ("size", points))

    def color(self, _color: Token, value: Token) -> tuple[str, Any]:
        text = str(value).lower()
        if text.startswith("#"):
            rgb = tuple(int(text[i : i + 2], 16) / 255 for i in (1, 3, 5))
            return ("style", ("color", rgb))
        return ("style", ("color", _NAMED_COLORS[text]))

    def font(self, _font: Token, family: Token) -> tuple[str, Any]:
        return ("style", ("font", _unquote(family)))


def _parts(children: list[Any], kind: str) -> list[Any]:
    return [value for item in children if isinstance(item, tuple) and item[0] == kind for value in [item[1]]]


def _scope(children: list[Any]) -> tuple[str, Any] | None:
    scopes = _parts(children, "scope")
    if len(scopes) > 1:
        raise CommandError("name the pages once, e.g. on pages 1-3,5")
    return scopes[0] if scopes else None


def _pages(scope: tuple[str, Any] | None, page_count: int) -> list[int] | None:
    """0-based page indices a scope names, or None for every page."""
    if scope is None or scope[0] == "all":
        return None
    kind, numbers = scope
    if kind == "odd":
        return list(range(0, page_count, 2))
    if kind == "even":
        return list(range(1, page_count, 2))
    bad = [n for n in numbers if not 1 <= n <= page_count]
    if bad:
        raise CommandError(f"page {bad[0]} doesn't exist: the document has {page_count} page(s)")
    return sorted({n - 1 for n in numbers})


def _describe_pages(pages: list[int] | None) -> str:
    if pages is None:
        return "on every page"
    if len(pages) == 1:
        return f"on page {pages[0] + 1}"
    return "on pages " + ", ".join(str(p + 1) for p in pages)


def _per_page(op: dict[str, Any], pages: list[int] | None) -> list[dict[str, Any]]:
    if pages is None:
        return [{**op, "page_index": None}]
    return [{**op, "page_index": page} for page in pages]


def parse_command(text: str, *, page_count: int, current_page: int = 0) -> CommandPlan:
    """Parse one command into a plan. `current_page` (0-based) is where "insert" goes when
    the command names no page. Raises CommandError, with suggestions, if it doesn't parse."""
    source = text.strip()
    if not 0 <= current_page < page_count:
        raise CommandError(f"page {current_page + 1} doesn't exist: the document has {page_count} page(s)")
    if not source:
        raise CommandError("type a command", suggestions=EXAMPLES[:3])
    first = source.split()[0].lower()
    if first in LATER_ACTIONS:
        raise CommandError(
            f"'{first}' isn't available yet: it is planned for {LATER_ACTIONS[first]}",
            suggestions=[],
        )
    try:
        tree = _PARSER.parse(source)
    except UnexpectedInput as exc:
        raise CommandError(_explain(source, exc), suggestions=_suggest(source)) from exc
    command = tree.children[0]
    if not isinstance(command, Tree):  # start always wraps exactly one command rule
        raise CommandError("couldn't understand the command", suggestions=_suggest(source))
    kind = str(command.data)
    if kind in ("undo", "redo"):
        return CommandPlan(text=source, description=f"{kind.capitalize()} the last change", special=kind)
    try:
        children = _Build().transform(command).children
    except VisitError as exc:  # a CommandError raised while building (bad regex, bad range, ...)
        if isinstance(exc.orig_exc, CommandError):
            raise exc.orig_exc from None
        raise
    return _BUILDERS[kind](source, children, page_count, current_page)


def _find_options(target: dict[str, Any], children: list[Any], *, allowed: tuple[str, ...]) -> dict[str, Any]:
    options = dict(_parts(children, "option"))
    if target.get("ignore_case"):
        options["case_sensitive"] = False
    refused = [name for name in options if name not in allowed]
    if refused:
        word = {"fit": "fit", "whole_word": "whole word", "case_sensitive": "ignoring case"}[refused[0]]
        raise CommandError(f'"{word}" doesn\'t apply to this command')
    return options


def _describe_options(options: dict[str, Any]) -> str:
    words = []
    if options.get("whole_word"):
        words.append("whole words only")
    if options.get("case_sensitive") is False:
        words.append("ignoring case")
    if options.get("fit"):
        words.append("fitted to the original width")
    return "".join(f", {word}" for word in words)


def _strings(children: list[Any]) -> list[str]:
    return [_unquote(c) for c in children if isinstance(c, Token) and c.type == "STRING"]


def _styles(children: list[Any]) -> dict[str, Any]:
    styles: dict[str, Any] = {}
    for key, value in _parts(children, "style"):
        if key in styles:
            raise CommandError(f'"{key}" is given twice; say it once')
        styles[key] = value
    return styles


def _replace(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    (target,) = _parts(children, "target")
    (replacement,) = _strings(children)
    pages = _pages(_scope(children), page_count)
    options = _find_options(target, children, allowed=("whole_word", "case_sensitive", "fit"))
    op = {"op": "replace_text", "match": target["match"], "mode": target["mode"], "replacement": replacement, **options}
    return CommandPlan(
        text=source,
        description=f'Replace {target["label"]} with "{replacement}" {_describe_pages(pages)}, matching its style'
        + _describe_options(options),
        ops=_per_page(op, pages),
    )


def _delete(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    (target,) = _parts(children, "target")
    pages = _pages(_scope(children), page_count)
    options = _find_options(target, children, allowed=("whole_word", "case_sensitive"))
    op = {"op": "delete_text", "match": target["match"], "mode": target["mode"], **options}
    return CommandPlan(
        text=source,
        description=f"Delete {target['label']} {_describe_pages(pages)}" + _describe_options(options),
        ops=_per_page(op, pages),
    )


def _insert(source: str, children: list[Any], page_count: int, current: int) -> CommandPlan:
    (new_text,) = _strings(children)
    if not new_text:
        raise CommandError("there is no text to insert")
    (placement,) = _parts(children, "placement")
    style = _styles(children)
    pages = _pages(_scope(children), page_count)
    if pages is None:
        pages = [current] if _scope(children) is None else list(range(page_count))
    changes = {key: value for key, value in style.items() if key != "match_style"}
    op: dict[str, Any] = {"op": "insert_text", "text": new_text, **placement, **changes}
    if "position" in placement and not ("font" in op and "size" in op):
        raise CommandError(
            'text placed "at" a point needs a font and a size, e.g. insert "Note" at 72, 700 font "Helvetica" size 12',
            suggestions=['insert "Note" at 72, 700 font "Helvetica" size 12'],
        )
    where = f'{placement["anchor"]} "{placement["reference_match"]}"' if "anchor" in placement else "at the point"
    if "anchor" in placement:
        style_words = "in its style"
        if changes:
            style_words += " with " + ", ".join(_describe_style(key, value) for key, value in changes.items())
    else:
        style_words = "in " + ", ".join(_describe_style(key, value) for key, value in changes.items())
    return CommandPlan(
        text=source,
        description=f'Insert "{new_text}" {where} {_describe_pages(pages)}, {style_words}',
        ops=[{**op, "page_index": page} for page in pages],
    )


def _set(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    (target,) = _parts(children, "target")
    style = _styles(children)
    if style.pop("match_style", False):
        raise CommandError('"match style" only applies to insert; set changes the style you give it')
    if not style:
        raise CommandError('say what to change, e.g. set bold size 12 for "Total"')
    pages = _pages(_scope(children), page_count)
    options = _find_options(target, children, allowed=("whole_word", "case_sensitive"))
    op = {"op": "restyle_text", "match": target["match"], "mode": target["mode"], **style, **options}
    changes = ", ".join(_describe_style(key, value) for key, value in style.items())
    return CommandPlan(
        text=source,
        description=f"Set {changes} for {target['label']} {_describe_pages(pages)}" + _describe_options(options),
        ops=_per_page(op, pages),
    )


def _describe_style(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return key if value else f"not {key}"
    if key == "color":
        return "color #" + "".join(f"{round(channel * 255):02x}" for channel in value)
    if key == "font":
        return f'font "{value}"'
    return f"{key} {value:g}"


def _pageset(children: list[Any], page_count: int) -> list[int]:
    (pageset,) = _parts(children, "pageset")
    pages = _pages(pageset, page_count)
    return list(range(page_count)) if pages is None else pages


def _tokens(children: list[Any], kind: str) -> list[Token]:
    return [c for c in children if isinstance(c, Token) and c.type == kind]


def _numbers(children: list[Any]) -> list[float]:
    return [c for c in children if isinstance(c, float)]


def _page_list(pages: list[int]) -> str:
    return ", ".join(str(p + 1) for p in pages)


def _relation(children: list[Any], allowed: tuple[str, ...] = ("after", "before")) -> str:
    (token,) = _tokens(children, "RELATION")
    relation = str(token).lower()
    if relation not in allowed:
        raise CommandError(f'use "after" or "before" a page here, not "{relation}"')
    return relation


def _target_page(children: list[Any], page_count: int) -> int:
    number = _page_number(_tokens(children, "NUMBER")[-1])
    if not 1 <= number <= page_count:
        raise CommandError(f"page {number} doesn't exist: the document has {page_count} page(s)")
    return number - 1


def _delete_pages(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    pages = _pageset(children, page_count)
    if len(pages) == page_count:
        raise CommandError("can't delete every page: a PDF needs at least one")
    return CommandPlan(source, f"Delete page(s) {_page_list(pages)}", [{"op": "delete_pages", "page_indices": pages}])


def _rotate_pages(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    pages = _pageset(children, page_count)
    numbers = _numbers(children)
    degrees = numbers[0] if numbers else 90.0
    if degrees != int(degrees) or int(degrees) % 90:
        raise CommandError(f"pages turn by multiples of 90 degrees, not {degrees:g}")
    turn = int(degrees) % 360
    direction = "clockwise"
    if _tokens(children, "COUNTERCLOCKWISE"):
        turn, direction = (360 - turn) % 360, "counterclockwise"
    op = {"op": "rotate_pages", "page_indices": pages, "degrees": turn}
    return CommandPlan(source, f"Rotate page(s) {_page_list(pages)} {int(degrees)} degrees {direction}", [op])


def _move_relative(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    pages = _pageset(children, page_count)
    relation = _relation(children)
    anchor = _target_page(children, page_count)
    if anchor in pages:
        raise CommandError(f"page {anchor + 1} is one of the pages being moved")
    rest = [p for p in range(page_count) if p not in set(pages)]
    to = rest.index(anchor) + (1 if relation == "after" else 0)
    op = {"op": "move_pages", "page_indices": pages, "to": to}
    return CommandPlan(source, f"Move page(s) {_page_list(pages)} {relation} page {anchor + 1}", [op])


def _move_edge(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    pages = _pageset(children, page_count)
    to_end = bool(_tokens(children, "END"))
    rest = page_count - len(set(pages))
    op = {"op": "move_pages", "page_indices": pages, "to": rest if to_end else 0}
    where = "to the end" if to_end else "to the start"
    return CommandPlan(source, f"Move page(s) {_page_list(pages)} {where}", [op])


def _duplicate_pages(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    pages = _pageset(children, page_count)
    numbers = _numbers(children)
    copies = numbers[0] if numbers else 1.0
    if copies != int(copies) or not 1 <= copies <= 100:
        raise CommandError(f"copies must be a whole number from 1 to 100, not {copies:g}")
    op = {"op": "duplicate_pages", "page_indices": pages, "copies": int(copies)}
    times = "" if copies == 1 else f" {int(copies)} times"
    return CommandPlan(source, f"Duplicate page(s) {_page_list(pages)}{times}", [op])


def _insert_blank(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    count = _numbers(children)[0]
    if count != int(count) or not 1 <= count <= 1000:
        raise CommandError(f"the number of pages must be a whole number from 1 to 1000, not {count:g}")
    relation = _relation(children)
    anchor = _target_page(children, page_count)
    at = anchor + 1 if relation == "after" else anchor
    op = {"op": "insert_pages", "at": at, "count": int(count)}
    return CommandPlan(source, f"Insert {int(count)} blank page(s) {relation} page {anchor + 1}", [op])


def _extract_pages(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    pages = _pageset(children, page_count)
    (out,) = _strings(children)
    if not out:
        raise CommandError("give the file to extract to")
    op = {"op": "extract_pages", "page_indices": pages, "out": out}
    return CommandPlan(source, f'Extract page(s) {_page_list(pages)} to "{out}" (this document is unchanged)', [op])


def _split_every(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    every = _numbers(children)[0]
    if every != int(every) or every < 1:
        raise CommandError(f"split every whole number of pages, not {every:g}")
    (folder,) = _strings(children)
    op = {"op": "split", "out_dir": folder, "every": int(every)}
    parts = -(-page_count // int(every))
    return CommandPlan(source, f'Split into {parts} file(s) of {int(every)} page(s) in "{folder}"', [op])


def _split_bookmarks(source: str, children: list[Any], _page_count: int, _current: int) -> CommandPlan:
    (folder,) = _strings(children)
    op = {"op": "split", "out_dir": folder, "by_bookmarks": True}
    return CommandPlan(source, f'Split at each top-level bookmark into files in "{folder}"', [op])


def _merge_doc(source: str, children: list[Any], page_count: int, _current: int) -> CommandPlan:
    (path,) = _strings(children)
    op: dict[str, Any] = {"op": "merge", "path": path}
    where = "at the end"
    if _tokens(children, "RELATION"):
        relation = _relation(children)
        anchor = _target_page(children, page_count)
        op["at"] = anchor + 1 if relation == "after" else anchor
        where = f"{relation} page {anchor + 1}"
    return CommandPlan(source, f'Merge "{path}" {where}', [op])


def _remove_blank(source: str, _children: list[Any], _page_count: int, _current: int) -> CommandPlan:
    return CommandPlan(source, "Remove blank pages (no text, nearly all white)", [{"op": "remove_blank_pages"}])


_BUILDERS = {
    "replace": _replace,
    "delete": _delete,
    "insert": _insert,
    "set": _set,
    "delete_pages": _delete_pages,
    "rotate_pages": _rotate_pages,
    "move_relative": _move_relative,
    "move_edge": _move_edge,
    "duplicate_pages": _duplicate_pages,
    "insert_blank": _insert_blank,
    "extract_pages": _extract_pages,
    "split_every": _split_every,
    "split_bookmarks": _split_bookmarks,
    "merge_doc": _merge_doc,
    "remove_blank": _remove_blank,
}


def _explain(source: str, exc: UnexpectedInput) -> str:
    column = getattr(exc, "column", None)
    near = source[column - 1 : column + 11] if isinstance(column, int) and column > 0 else ""
    first = source.split()[0].lower() if source.split() else ""
    if first not in VERBS:
        return f"'{first}' isn't a command"
    return f"couldn't understand the command near “{near.strip() or 'the end'}”"


def _suggest(source: str) -> list[str]:
    """The closest valid commands (CMD-06). Shown to the user, never run on their behalf."""
    first = source.split()[0].lower() if source.split() else ""
    suggestions: list[str] = []
    verb = process.extractOne(first, VERBS, scorer=fuzz.ratio, score_cutoff=60) if first else None
    if verb and verb[0] != first and first not in LATER_ACTIONS:
        fixed = verb[0] + source[len(first) :]
        suggestions.append(fixed)
    for example, _score, _index in process.extract(source, EXAMPLES, scorer=fuzz.token_set_ratio, limit=3):
        if example not in suggestions:
            suggestions.append(example)
    return suggestions[:3]


def complete(text: str) -> list[str]:
    """CMD-04: what can come next after `text` -- keywords and placeholders -- for the
    command bar's autocomplete. A half-typed last word is completed from the keywords."""
    stripped = text.lstrip()
    if not stripped:
        return VERBS
    partial = "" if text.endswith((" ", ",", "-")) else stripped.split()[-1]
    head = stripped[: len(stripped) - len(partial)] if partial else stripped
    interactive = _PARSER.parse_interactive(head)
    try:
        for token in interactive.lexer_thread.lex(interactive.parser_state):
            interactive.feed_token(token)
    except UnexpectedInput:
        return []
    options: list[str] = []
    for name in sorted(interactive.accepts()):
        if name == "$END":
            continue
        if name in _TOKEN_TEXT:
            options.append(_TOKEN_TEXT[name])
        else:
            options.extend(KEYWORDS.get(name, ()))
    options = list(dict.fromkeys(options))
    if partial:
        return [option for option in options if option.startswith(partial.lower()) and option != partial.lower()]
    return options
