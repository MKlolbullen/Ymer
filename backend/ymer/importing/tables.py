"""CSV to a database spec (the shape :func:`ymer.ai.generate.create_database` takes).

The first column is the title. Every other column gets the narrowest type all of its
values fit: checkbox (Yes/No), number, date, URL, select (a few repeated values),
multi-select (comma-separated values that repeat), and text otherwise. Notion's export
formats are understood: ``Yes``/``No`` checkboxes, ``November 25, 2026`` dates (with an
optional time, or a ``start → end`` range of which the start is kept), and relation cells
written as ``Title (Other%20Page%20<id>.md)``, which become plain titles.

A template can pin the types instead of having them guessed, with a ``<name>.schema.json``
next to the CSV: ``{"properties": [{"name", "type", "options"?}]}``.
"""

import csv
import io
import re
from datetime import datetime
from typing import Any

MAX_COLUMNS = 40
MAX_ROWS = 5000

_NOTION_LINK = re.compile(r"\s*\((?:[^()]*?\.(?:md|csv))\)")
_NUMBER = re.compile(
    r"^[-+]?[$€£¥]?\s?\d{1,3}(?:[ ,]\d{3})*(?:\.\d+)?\s?%?$|^[-+]?\d+(?:\.\d+)?%?$"
)
_URL = re.compile(r"^(https?://|mailto:)\S+$", re.IGNORECASE)
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%m/%d/%Y",
    "%B %d, %Y %I:%M %p",
    "%b %d, %Y %I:%M %p",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
)
_SELECT_HINTS = {"status", "stage", "priority", "type", "category", "state", "kind"}
_MULTI_HINTS = {"tags", "labels", "topics", "categories"}


def _clean(cell: str) -> str:
    return " ".join(_NOTION_LINK.sub("", cell or "").split())


def parse_date(value: str) -> str | None:
    """An ISO date (or timestamp) for a date written the way Notion or people write it."""
    value = value.split("→", 1)[0].strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        has_time = "%H" in fmt or "%I" in fmt
        return parsed.isoformat() if has_time else parsed.date().isoformat()
    return None


def parse_number(value: str) -> str | None:
    if not _NUMBER.match(value.strip()):
        return None
    cleaned = re.sub(r"[$€£¥%\s,]", "", value)
    try:
        float(cleaned)
    except ValueError:
        return None
    return cleaned


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def infer_type(name: str, values: list[str]) -> str:
    present = [v for v in values if v]
    if not present:
        return "text"
    lowered = {v.lower() for v in present}
    if lowered <= {"yes", "no", "true", "false", "✓", "x"} and len(present) == len(values):
        return "checkbox"
    if all(parse_number(v) is not None for v in present):
        return "number"
    if all(parse_date(v) is not None for v in present):
        return "date"
    if all(_URL.match(v) for v in present):
        return "url"
    if max(len(v) for v in present) > 60:
        return "text"
    key = name.strip().lower()
    distinct = set(present)
    parts = [p for v in present for p in _split(v)]
    if key in _MULTI_HINTS or (
        any("," in v for v in present) and len(set(parts)) < len(parts) and len(set(parts)) <= 50
    ):
        return "multi_select"
    if key in _SELECT_HINTS and len(distinct) <= 50:
        return "select"
    if len(present) >= 4 and len(distinct) <= min(12, len(present) // 2):
        return "select"
    return "text"


def _value(kind: str, raw: str) -> str:
    if not raw:
        return ""
    if kind == "checkbox":
        return "yes" if raw.lower() in {"yes", "true", "✓", "x"} else ""
    if kind == "number":
        return parse_number(raw) or ""
    if kind == "date":
        return parse_date(raw) or ""
    return raw


def read_rows(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0][:MAX_COLUMNS]]
    body = [[_clean(c) for c in row[: len(header)]] for row in rows[1 : MAX_ROWS + 1]]
    for row in body:
        row.extend([""] * (len(header) - len(row)))
    return header, body


def csv_spec(text: str, schema: dict | None = None) -> dict[str, Any]:
    """A database spec from CSV text: properties with types, and rows of values."""
    header, body = read_rows(text)
    names: list[str] = []
    for i, name in enumerate(header):
        name = name or f"Column {i + 1}"
        while name.lower() in {n.lower() for n in names}:
            name += " (2)"
        names.append(name[:200])

    pinned = {}
    if schema and isinstance(schema.get("properties"), list):
        pinned = {
            str(p.get("name", "")).lower(): p for p in schema["properties"] if isinstance(p, dict)
        }

    properties: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        column = [row[i] for row in body]
        if name.lower() in pinned:
            spec = dict(pinned[name.lower()])
            spec["name"] = name
        elif i == 0:
            spec = {"name": name, "type": "title"}
        else:
            spec = {"name": name, "type": infer_type(name, column)}
        if spec["type"] in ("select", "multi_select") and "options" not in spec:
            seen: dict[str, None] = {}
            for value in column:
                for part in _split(value) if spec["type"] == "multi_select" else [value]:
                    if part:
                        seen.setdefault(part, None)
            spec["options"] = list(seen)[:50]
        properties.append(spec)
    # Pinned properties with no column still belong to the database.
    for key, spec in pinned.items():
        if key not in {n.lower() for n in names}:
            properties.append(dict(spec))

    kinds = [p["type"] for p in properties]
    rows = [
        {
            "values": [
                {"property": names[i], "value": _value(kinds[i], cell)}
                for i, cell in enumerate(row)
                if cell
            ]
        }
        for row in body
    ]
    return {"properties": properties, "rows": rows}
