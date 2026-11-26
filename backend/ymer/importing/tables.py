"""CSV to a database spec (the shape :func:`ymer.ai.generate.create_database` takes).

The first column is the title. Every other column gets the narrowest type all of its
values fit: checkbox (Yes/No), number, date, URL, select (a few repeated values),
multi-select (comma-separated values that repeat), and text otherwise. Notion's export
formats are understood: ``Yes``/``No`` checkboxes, ``November 25, 2026`` dates (with an
optional time, or a ``start → end`` range of which the start is kept), and relation cells
written as ``Title (Other%20Page%20<id>.md)``, which become plain titles.

Columns of ``s3://``/``gs://`` paths become file properties, ``start → end`` dates become
date ranges, and columns named like a person (``Owner``, ``Lead``, ``Advisor``…) become
person properties holding names (matched to members by name; see :mod:`ymer.databases`).

A template can pin the types instead of having them guessed, with a ``<name>.schema.json``
next to the CSV: ``{"properties": [{"name", "type", "options"?}]}``.
"""

import csv
import io
import json
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
_PERSON_HINTS = {
    "lead",
    "owner",
    "owners",
    "assignee",
    "assignees",
    "assigned to",
    "advisor",
    "adviser",
    "instructor",
    "teacher",
    "manager",
    "reviewer",
    "reviewers",
    "responsible",
    "person",
    "people",
    "author",
    "created by",
}
_FILE_PATH = re.compile(r"^(s3|gs|minio)://\S+$", re.IGNORECASE)
_MULTI_HINTS = {"tags", "labels", "topics", "categories"}


def _clean(cell: str) -> str:
    return " ".join(_NOTION_LINK.sub("", cell or "").split())


def parse_date(value: str) -> str | None:
    """An ISO date (or timestamp) for a date written the way Notion or people write it."""
    value = value.split("→", 1)[0].strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$", value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return None
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


def _parts(values: list[str]) -> list[str]:
    return [p for v in values for p in re.split(r"[\s,]+", v) if p]


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
        if any("→" in v for v in present):
            return "date_range"
        return "date"
    if all(_FILE_PATH.match(v) for v in _parts(present)):
        return "file"
    if all(_URL.match(v) for v in present):
        return "url"
    if max(len(v) for v in present) > 60:
        return "text"
    key = name.strip().lower()
    if " ".join(key.replace("_", " ").split()) in _PERSON_HINTS:
        return "person"
    distinct = set(present)
    parts = [p for v in present for p in _split(v)]
    if key in _MULTI_HINTS or (
        any("," in v for v in present) and len(set(parts)) < len(parts) and len(set(parts)) <= 50
    ):
        return "multi_select"
    last_word = key.replace("_", " ").split()[-1] if key.split() else key
    if (key in _SELECT_HINTS or last_word in _SELECT_HINTS) and len(distinct) <= 50:
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
    if kind == "date_range":
        ends = [parse_date(part) for part in raw.split("→", 1)]
        return " → ".join(e for e in ends if e)
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
    return table_spec(header, body, schema)


def json_rows(data: Any) -> tuple[list[str], list[list[str]]] | None:
    """A table from JSON: a list of flat objects. None when it is not one."""
    if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
        return None
    header: list[str] = []
    for record in data[:MAX_ROWS]:
        for key in record:
            if key not in header and len(header) < MAX_COLUMNS:
                header.append(str(key))
    body = [[_json_cell(record.get(h)) for h in header] for record in data[:MAX_ROWS]]
    return header, body


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(value)  # keep 15.0 as written: the type is still a number
    if isinstance(value, list):
        return ", ".join(_json_cell(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return _clean(str(value))


def table_spec(
    header: list[str], body: list[list[str]], schema: dict | None = None
) -> dict[str, Any]:
    """A database spec from a table: the first column is the title."""
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
