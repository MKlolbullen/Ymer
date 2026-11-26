"""Formula properties: a small, safe expression language over a row's other properties.

Formulas are written the way Notion writes them::

    prop("Score") / prop("Max Points") * 100
    ifs(prop("Grade") >= 90, "A", prop("Grade") >= 80, "B", "F")
    if(empty(prop("Grade")), "", round(prop("Grade"), 1))

Nothing is ever passed to Python's ``eval``: :func:`compile_formula` tokenises and parses
the text into a tree of tuples, and :func:`evaluate` walks it. Properties are referenced
by name; :func:`rename_property` rewrites the references when a property is renamed.

Values inside a formula are plain: numbers, text, true/false, ISO date strings, lists
(multi-select names, rollup "show original"), date ranges as ``{"start", "end"}`` and
``None`` for empty. Arithmetic or comparison with an empty value gives empty (or false),
so a formula over a blank row stays blank instead of failing. A list of one value (a
lookup through a relation) counts as that value.
"""

import math
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

MAX_LENGTH = 2000
MAX_DEPTH = 60


class FormulaError(ValueError):
    """The formula cannot be parsed, or refers to something that does not exist."""


# --- tokens ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|\.\d+)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<op>==|!=|>=|<=|&&|\|\||[-+*/%^()<>!,=])
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


def _tokens(text: str) -> list[tuple[str, Any, int]]:
    out: list[tuple[str, Any, int]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise FormulaError(f"Unexpected character {text[pos]!r} at position {pos + 1}.")
        kind = match.lastgroup or ""
        raw = match.group(0)
        if kind == "number":
            out.append(("number", float(raw), pos))
        elif kind == "string":
            out.append(("string", _unquote(raw), pos))
        elif kind == "op":
            out.append(("op", "==" if raw == "=" else raw, pos))
        elif kind == "name":
            out.append(("name", raw, pos))
        pos = match.end()
    out.append(("end", None, len(text)))
    return out


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- parsing --------------------------------------------------------------------------

# A node is a tuple: ("lit", value) | ("prop", name) | ("call", fn, [args])
#                  | ("bin", op, left, right) | ("neg", node) | ("not", node)
Node = tuple

_KEYWORDS = {"true": True, "false": False, "null": None}
_WORD_OPS = {"and": "&&", "or": "||"}


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.i = 0
        self.depth = 0

    def peek(self) -> tuple[str, Any, int]:
        return self.tokens[self.i]

    def take(self) -> tuple[str, Any, int]:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def at(self, *ops: str) -> str | None:
        kind, value, _ = self.peek()
        if kind == "op" and value in ops:
            return value
        if kind == "name" and _WORD_OPS.get(value.lower()) in ops:
            return _WORD_OPS[value.lower()]
        if kind == "name" and value.lower() == "not" and "!" in ops:
            return "!"
        return None

    def expect(self, op: str) -> None:
        kind, value, pos = self.take()
        if kind != "op" or value != op:
            found = "the end" if kind == "end" else repr(value)
            raise FormulaError(f"Expected {op!r} at position {pos + 1}, found {found}.")

    def parse(self) -> Node:
        node = self.expr()
        kind, value, pos = self.peek()
        if kind != "end":
            raise FormulaError(f"Unexpected {value!r} at position {pos + 1}.")
        return node

    def expr(self) -> Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError("That formula is nested too deeply.")
        try:
            return self.or_()
        finally:
            self.depth -= 1

    def or_(self) -> Node:
        node = self.and_()
        while self.at("||"):
            self.take()
            node = ("bin", "||", node, self.and_())
        return node

    def and_(self) -> Node:
        node = self.not_()
        while self.at("&&"):
            self.take()
            node = ("bin", "&&", node, self.not_())
        return node

    def not_(self) -> Node:
        if self.at("!"):
            self.take()
            return ("not", self.not_())
        return self.compare()

    def compare(self) -> Node:
        node = self.add()
        if op := self.at("==", "!=", ">=", "<=", ">", "<"):
            self.take()
            node = ("bin", op, node, self.add())
        return node

    def add(self) -> Node:
        node = self.mul()
        while op := self.at("+", "-"):
            self.take()
            node = ("bin", op, node, self.mul())
        return node

    def mul(self) -> Node:
        node = self.unary()
        while op := self.at("*", "/", "%"):
            self.take()
            node = ("bin", op, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.at("-"):
            self.take()
            return ("neg", self.unary())
        if self.at("+"):
            self.take()
            return self.unary()
        return self.power()

    def power(self) -> Node:
        node = self.primary()
        if self.at("^"):
            self.take()
            node = ("bin", "^", node, self.unary())
        return node

    def primary(self) -> Node:
        kind, value, pos = self.take()
        if kind in {"number", "string"}:
            return ("lit", value)
        if kind == "op" and value == "(":
            node = self.expr()
            self.expect(")")
            return node
        if kind == "name":
            lowered = value.lower()
            if lowered in _KEYWORDS and not self.at("("):
                return ("lit", _KEYWORDS[lowered])
            if not self.at("("):
                raise FormulaError(
                    f"Unknown name {value!r} at position {pos + 1}. "
                    'Refer to properties as prop("Name").'
                )
            self.take()
            args: list[Node] = []
            if not self.at(")"):
                args.append(self.expr())
                while self.at(","):
                    self.take()
                    args.append(self.expr())
            self.expect(")")
            if lowered == "prop":
                if len(args) != 1 or args[0][0] != "lit" or not isinstance(args[0][1], str):
                    raise FormulaError('prop() takes a property name in quotes: prop("Name").')
                return ("prop", args[0][1])
            fn = FUNCTIONS.get(lowered)
            if fn is None:
                raise FormulaError(f"Unknown function {value}() at position {pos + 1}.")
            low, high = fn[1], fn[2]
            if len(args) < low or (high is not None and len(args) > high):
                expected = str(low) if low == high else f"{low} to {high or 'any number of'}"
                raise FormulaError(f"{value}() takes {expected} argument(s), not {len(args)}.")
            return ("call", lowered, args)
        if kind == "end":
            raise FormulaError("The formula ends too early.")
        raise FormulaError(f"Unexpected {value!r} at position {pos + 1}.")


@lru_cache(maxsize=512)
def compile_formula(text: str) -> Node:
    """Parse a formula; raises FormulaError with a message for the person writing it."""
    if not text or not text.strip():
        raise FormulaError('Write a formula, for example prop("Price") * 2.')
    if len(text) > MAX_LENGTH:
        raise FormulaError(f"Formulas can be at most {MAX_LENGTH} characters.")
    return _Parser(text).parse()


def references(node: Node) -> set[str]:
    """The property names a parsed formula reads."""
    found: set[str] = set()

    def walk(n: Node) -> None:
        match n[0]:
            case "prop":
                found.add(n[1])
            case "call":
                for arg in n[2]:
                    walk(arg)
            case "bin":
                walk(n[2])
                walk(n[3])
            case "neg" | "not":
                walk(n[1])

    walk(node)
    return found


def rename_property(text: str, old: str, new: str) -> str:
    """Rewrite ``prop("old")`` references to ``prop("new")``, leaving everything else."""
    try:
        tokens = _tokens(text)
    except FormulaError:
        return text
    out = []
    last = 0
    for i, (kind, value, pos) in enumerate(tokens):
        if (
            kind == "string"
            and value == old
            and i >= 2
            and tokens[i - 1][:2] == ("op", "(")
            and tokens[i - 2][0] == "name"
            and tokens[i - 2][1].lower() == "prop"
        ):
            end = _TOKEN.match(text, pos).end()  # type: ignore[union-attr]
            out.append(text[last:pos])
            out.append(_quote(new))
            last = end
    out.append(text[last:])
    return "".join(out)


# --- values ---------------------------------------------------------------------------


def _scalar(value: Any) -> Any:
    """A lookup through a relation is a list; one value counts as that value."""
    if isinstance(value, list):
        if not value:
            return None
        if len(value) == 1:
            return _scalar(value[0])
    if value == "":
        return None
    return value


def _num(value: Any) -> float | None:
    value = _scalar(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def _truthy(value: Any) -> bool:
    value = _scalar(value)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "false", "no", "0"}
    if isinstance(value, list | dict):
        return bool(value)
    return bool(value)


def to_text(value: Any) -> str:
    value = _scalar(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{round(value, 6):g}"
    if isinstance(value, list):
        return ", ".join(to_text(v) for v in value)
    if isinstance(value, dict) and "start" in value:
        return f"{value.get('start') or ''} → {value.get('end') or ''}".strip(" →")
    return str(value)


def _as_date(value: Any) -> datetime | None:
    value = _scalar(value)
    if isinstance(value, dict):
        value = value.get("start")
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), datetime.min.time())
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _date_out(moment: datetime, like: Any) -> str:
    like = _scalar(like)
    if isinstance(like, dict):
        like = like.get("start")
    if isinstance(like, str) and len(like) == 10:
        return moment.date().isoformat()
    return moment.isoformat()


def _compare(op: str, left: Any, right: Any) -> bool:
    left, right = _scalar(left), _scalar(right)
    if op in {"==", "!="}:
        if isinstance(left, int | float) or isinstance(right, int | float):
            a, b = _num(left), _num(right)
            same = a is not None and b is not None and a == b
        elif left is None or right is None:
            same = left is None and right is None
        else:
            same = to_text(left).lower() == to_text(right).lower()
        return same if op == "==" else not same
    if left is None or right is None:
        return False
    a, b = _num(left), _num(right)
    if a is None or b is None or isinstance(left, str) and isinstance(right, str):
        a, b = to_text(left).lower(), to_text(right).lower()  # type: ignore[assignment]
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]  # type: ignore[operator]


def _arith(op: str, left: Any, right: Any) -> Any:
    left, right = _scalar(left), _scalar(right)
    if op == "+" and (isinstance(left, str) or isinstance(right, str)):
        return to_text(left) + to_text(right)
    a, b = _num(left), _num(right)
    if a is None or b is None:
        return None
    match op:
        case "+":
            return a + b
        case "-":
            return a - b
        case "*":
            return a * b
        case "/":
            return None if b == 0 else a / b
        case "%":
            return None if b == 0 else math.fmod(a, b)
        case "^":
            try:
                result = a**b
            except (OverflowError, ZeroDivisionError):
                return None
            return result if isinstance(result, float | int) else None
    return None


# --- functions ------------------------------------------------------------------------

_UNITS = {
    "years": 365.25,
    "year": 365.25,
    "months": 30.4375,
    "month": 30.4375,
    "weeks": 7,
    "week": 7,
    "days": 1,
    "day": 1,
    "hours": 1 / 24,
    "hour": 1 / 24,
    "minutes": 1 / 1440,
    "minute": 1 / 1440,
}


def _round(x: Any, digits: Any = 0) -> Any:
    n, d = _num(x), _num(digits)
    if n is None:
        return None
    d = int(d or 0)
    # Round half away from zero, the way people (and spreadsheets) expect.
    factor = 10**d
    return math.floor(abs(n) * factor + 0.5) / factor * (1 if n >= 0 else -1)


def _numbers(args: list) -> list[float]:
    out = []
    for arg in args:
        items = arg if isinstance(arg, list) else [arg]
        out.extend(n for n in (_num(i) for i in items) if n is not None)
    return out


def _date_between(a: Any, b: Any, unit: Any = "days") -> Any:
    first, second = _as_date(a), _as_date(b)
    if first is None or second is None:
        return None
    unit = to_text(unit).lower() or "days"
    days = (first - second).total_seconds() / 86400
    if unit in {"months", "month"}:
        months = (first.year - second.year) * 12 + first.month - second.month
        if first.day < second.day and months > 0:
            months -= 1
        return months
    if unit in {"years", "year"}:
        years = first.year - second.year
        if (first.month, first.day) < (second.month, second.day) and years > 0:
            years -= 1
        return years
    return math.trunc(days / _UNITS.get(unit, 1))


def _date_add(d: Any, amount: Any, unit: Any = "days") -> Any:
    moment, n = _as_date(d), _num(amount)
    if moment is None or n is None:
        return None
    unit = to_text(unit).lower() or "days"
    if unit in {"months", "month", "years", "year"}:
        months = int(n) * (12 if unit.startswith("year") else 1)
        year, month = divmod(moment.month - 1 + months, 12)
        target_year = moment.year + year
        day = min(moment.day, _month_days(target_year, month + 1))
        moved = moment.replace(year=target_year, month=month + 1, day=day)
    else:
        moved = moment + timedelta(days=n * _UNITS.get(unit, 1))
    return _date_out(moved, d)


def _month_days(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def _ifs(*args: Any) -> Any:
    raise AssertionError("ifs is evaluated lazily")  # pragma: no cover


def _contains(haystack: Any, needle: Any) -> bool:
    if isinstance(haystack, list):
        want = to_text(needle).lower()
        return any(to_text(v).lower() == want for v in haystack)
    return to_text(needle).lower() in to_text(haystack).lower()


def _length(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return len(to_text(value))


def _to_number(value: Any) -> Any:
    if isinstance(_scalar(value), str):
        match = re.search(r"-?\d+(?:[.,]\d+)?", _scalar(value))
        return float(match.group(0).replace(",", ".")) if match else None
    return _num(value)


def _date_part(part: str) -> Callable[[Any], Any]:
    def get(value: Any) -> Any:
        moment = _as_date(value)
        if moment is None:
            return None
        return {
            "year": moment.year,
            "month": moment.month,
            "day": moment.day,
            "weekday": moment.isoweekday(),
        }[part]

    return get


def _range_part(key: str) -> Callable[[Any], Any]:
    def get(value: Any) -> Any:
        value = _scalar(value)
        if isinstance(value, dict):
            return value.get(key) or (value.get("start") if key == "end" else None)
        return value if isinstance(value, str) else None

    return get


def _format_date(value: Any) -> Any:
    moment = _as_date(value)
    return moment.strftime("%b %d, %Y") if moment else None


# name -> (implementation, min args, max args or None for any)
FUNCTIONS: dict[str, tuple[Callable[..., Any], int, int | None]] = {
    "if": (lambda c, a, b=None: None, 2, 3),  # lazy, see _eval
    "ifs": (_ifs, 2, None),  # lazy
    "and": (lambda *a: all(_truthy(x) for x in a), 1, None),
    "or": (lambda *a: any(_truthy(x) for x in a), 1, None),
    "not": (lambda a: not _truthy(a), 1, 1),
    "empty": (lambda a: _scalar(a) is None or a == [] or a == {}, 1, 1),
    "round": (_round, 1, 2),
    "floor": (lambda a: None if _num(a) is None else math.floor(_num(a)), 1, 1),  # type: ignore[arg-type]
    "ceil": (lambda a: None if _num(a) is None else math.ceil(_num(a)), 1, 1),  # type: ignore[arg-type]
    "abs": (lambda a: None if _num(a) is None else abs(_num(a)), 1, 1),  # type: ignore[arg-type]
    "sqrt": (lambda a: None if (_num(a) or -1) < 0 else math.sqrt(_num(a)), 1, 1),  # type: ignore[arg-type]
    "pow": (lambda a, b: _arith("^", a, b), 2, 2),
    "min": (lambda *a: min(_numbers(list(a)), default=None), 1, None),
    "max": (lambda *a: max(_numbers(list(a)), default=None), 1, None),
    "sum": (lambda *a: sum(_numbers(list(a))), 1, None),
    "average": (
        lambda *a: (sum(_numbers(list(a))) / len(_numbers(list(a)))) if _numbers(list(a)) else None,
        1,
        None,
    ),
    "concat": (lambda *a: "".join(to_text(x) for x in a), 1, None),
    "join": (lambda items, sep=", ": to_text(sep).join(to_text(x) for x in (items or [])), 1, 2),
    "length": (_length, 1, 1),
    "lower": (lambda a: to_text(a).lower(), 1, 1),
    "upper": (lambda a: to_text(a).upper(), 1, 1),
    "contains": (_contains, 2, 2),
    "replace": (lambda a, b, c: to_text(a).replace(to_text(b), to_text(c)), 3, 3),
    "format": (to_text, 1, 1),
    "totext": (to_text, 1, 1),
    "tonumber": (_to_number, 1, 1),
    "today": (lambda: date.today().isoformat(), 0, 0),
    "now": (lambda: datetime.now().replace(microsecond=0).isoformat(), 0, 0),
    "datebetween": (_date_between, 2, 3),
    "dateadd": (_date_add, 2, 3),
    "formatdate": (_format_date, 1, 1),
    "start": (_range_part("start"), 1, 1),
    "end": (_range_part("end"), 1, 1),
    "year": (_date_part("year"), 1, 1),
    "month": (_date_part("month"), 1, 1),
    "day": (_date_part("day"), 1, 1),
    "weekday": (_date_part("weekday"), 1, 1),
}


# --- evaluation -----------------------------------------------------------------------


def _eval(node: Node, lookup: Callable[[str], Any]) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "prop":
        return lookup(node[1])
    if kind == "neg":
        value = _num(_eval(node[1], lookup))
        return None if value is None else -value
    if kind == "not":
        return not _truthy(_eval(node[1], lookup))
    if kind == "bin":
        op = node[1]
        if op == "&&":
            return _truthy(_eval(node[2], lookup)) and _truthy(_eval(node[3], lookup))
        if op == "||":
            return _truthy(_eval(node[2], lookup)) or _truthy(_eval(node[3], lookup))
        left, right = _eval(node[2], lookup), _eval(node[3], lookup)
        if op in {"==", "!=", ">", "<", ">=", "<="}:
            return _compare(op, left, right)
        return _arith(op, left, right)
    # call
    name, args = node[1], node[2]
    if name == "if":
        if _truthy(_eval(args[0], lookup)):
            return _eval(args[1], lookup)
        return _eval(args[2], lookup) if len(args) > 2 else None
    if name == "ifs":
        for i in range(0, len(args) - 1, 2):
            if _truthy(_eval(args[i], lookup)):
                return _eval(args[i + 1], lookup)
        return _eval(args[-1], lookup) if len(args) % 2 else None
    values = [_eval(a, lookup) for a in args]
    return FUNCTIONS[name][0](*values)


def evaluate(node: Node, lookup: Callable[[str], Any]) -> Any:
    """Run a compiled formula; ``lookup(name)`` gives a property's plain value.

    Returns a number, text, true/false, a date string, a list, or None (empty, or the
    formula could not be computed for this row, such as a division by zero)."""
    try:
        result = _eval(node, lookup)
    except (ArithmeticError, ValueError, TypeError, RecursionError):
        return None
    return tidy(result)


def tidy(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value) if value.is_integer() and abs(value) < 2**53 else round(value, 6)
    if value == "" or value == []:
        return None
    if isinstance(value, list):
        return [tidy(v) for v in value]
    return value
