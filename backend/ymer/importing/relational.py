"""Linking imported tables into related databases.

An export from another system is often a set of tables that point at each other by id:
``assets.csv`` has an ``engagement_id`` column holding ids from ``engagements.csv``. When
an import holds such tables, :func:`link_tables` turns them into a relational workspace:

- Each table's own id column (its first column, named ``id`` or ``<something>_id``) is
  its key. A ``<thing>_id`` column whose values are keys of another table becomes a
  two-way relation to that table (``Engagement`` on Assets, ``Assets`` on Engagements).
  Several ids in one cell (``AST-001, AST-002``) link several rows.
- A column that only repeats what the related row already says is not kept as a copy:
  the related row's name (``student_name`` next to ``student_id``) is dropped, since the
  relation shows it, and any other copied value (``max_points`` from the assignment)
  becomes a lookup: a rollup that shows the related row's value, so it cannot drift.
- When a table's first column is an id and it has a ``name``/``title``-like column, that
  column becomes the title and the id stays as a text property.
- ``start_date``/``end_date`` (or ``<x>_start``/``<x>_end``) pairs become one date range;
  ``snake_case`` headers become ``Title Case`` names.

Columns of ``s3://`` paths become file properties and columns named like a person
(``lead``, ``owner``, ``advisor``…) become people matched by name; those apply to every
CSV import, in :mod:`ymer.importing.tables`.

The result is written into each database node's spec, for :mod:`ymer.importing.run`:
``relations`` ``[{name, target, reverse_name, cells}]`` (cells line up with ``rows``),
``lookups`` ``[{name, relation, target_property}]``, ``keys`` (each row's id, for rows
other tables point at) and ``order`` (property names, in the table's column order).
"""

import re
from dataclasses import dataclass, field
from typing import Any

from ymer.importing.tables import parse_date, parse_number, table_spec

MATCH_SHARE = 0.8  # of a column's ids that must be keys of the other table
TITLE_NAMES = (
    "name",
    "title",
    "full_name",
    "display_name",
    "identifier",
    "label",
    "subject",
    "summary",
    "headline",
)
_ACRONYMS = {
    "id",
    "ids",
    "url",
    "uri",
    "api",
    "ip",
    "pdf",
    "cvss",
    "cwe",
    "cve",
    "owasp",
    "sla",
    "gpa",
    "ects",
    "poc",
    "s3",
    "sso",
    "ci",
    "kpi",
    "sku",
    "vat",
    "json",
    "csv",
    "html",
    "ai",
    "llm",
    "mcp",
}
_SMALL = {"of", "and", "the", "to", "by", "in", "for", "on", "at", "a", "an", "or", "per"}
_SNAKE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_KEY = re.compile(r"^(?:id|.+_id)$", re.IGNORECASE)
_REF = re.compile(r"^(.+?)_ids?$", re.IGNORECASE)
_PAIR_PATTERNS = (
    (re.compile(r"^start_?(date|at|time)?$", re.I), re.compile(r"^end_?(date|at|time)?$", re.I)),
    (
        re.compile(r"^(.+?)_start(?:_date|_at)?$", re.I),
        re.compile(r"^(.+?)_end(?:_date|_at)?$", re.I),
    ),
    (re.compile(r"^start_(.+)$", re.I), re.compile(r"^end_(.+)$", re.I)),
    (re.compile(r"^(.+?)_from$", re.I), re.compile(r"^(.+?)_(?:to|until)$", re.I)),
)


def humanize(header: str) -> str:
    """``rules_of_engagement_pdf`` -> ``Rules of Engagement PDF``; other names unchanged."""
    if not _SNAKE.match(header):
        return header
    words = header.split("_")
    out = []
    for i, word in enumerate(words):
        if word in _ACRONYMS:
            out.append("ID" if word == "id" else word.upper())
        elif i and word in _SMALL:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def singular(title: str) -> str:
    if title.lower().endswith("ies"):
        return title[:-3] + "y"
    if title.lower().endswith(("sses", "xes", "ches", "shes")):
        return title[:-2]
    if title.lower().endswith("s") and not title.lower().endswith("ss"):
        return title[:-1]
    return title


def _parts(cell: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;|]", cell) if p.strip()]


def _same(a: str, b: str) -> bool:
    if a.strip() == b.strip():
        return True
    x, y = parse_number(a), parse_number(b)
    return x is not None and y is not None and float(x) == float(y)


@dataclass
class Column:
    header: str
    cells: list[str]
    label: str = ""


@dataclass
class Relation:
    column: Column
    target: "Table"
    label: str = ""


@dataclass
class Table:
    node: Any  # PlanNode
    columns: list[Column]
    rows: int
    key: Column | None = None
    title: Column | None = None
    relations: list[Relation] = field(default_factory=list)
    lookups: list[tuple[Column, Relation, Column]] = field(default_factory=list)
    dropped: list[Column] = field(default_factory=list)
    key_index: dict[str, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.node.title


def _find_key(table: Table) -> None:
    if not table.columns:
        return
    first = table.columns[0]
    if not _KEY.match(first.header):
        return
    present = [c.strip() for c in first.cells]
    if not all(present) or len(set(present)) != len(present):
        return
    table.key = first
    table.key_index = {v: i for i, v in enumerate(present)}


def _candidates(stem: str, tables: list[Table]) -> list[Table]:
    """Tables a ``<stem>_id`` column could point at, best guesses first."""
    stem = stem.lower()
    found = []
    for t in tables:
        if t.key is None:
            continue
        key = t.key.header.lower()
        names = {t.name.lower().replace(" ", "_"), singular(t.name).lower().replace(" ", "_")}
        if key in {f"{stem}_id", f"{stem}_ids"} or stem in names:
            found.insert(0, t)
        elif any(n.startswith(stem) for n in names) or key.startswith(stem):
            found.append(t)
    return found


def _find_relations(table: Table, tables: list[Table]) -> None:
    for column in table.columns:
        if column is table.key:
            continue
        match = _REF.match(column.header)
        if not match:
            continue
        ids = [p for cell in column.cells for p in _parts(cell)]
        if not ids:
            continue
        others = _candidates(match.group(1), tables) or [t for t in tables if t.key]
        for other in others:
            known = sum(1 for i in ids if i in other.key_index)
            if known >= MATCH_SHARE * len(ids):
                table.relations.append(Relation(column=column, target=other))
                break


def _relation_label(relation: Relation) -> str:
    stem = _REF.match(relation.column.header).group(1)  # type: ignore[union-attr]
    target = singular(relation.target.name)
    if target.lower().replace(" ", "_").startswith(stem.lower()):
        return target
    return humanize(stem)


def _copied_from(table: Table, column: Column) -> list[tuple[Relation, Column]]:
    """The related columns this one only repeats (same name, same value on every row)."""
    found = []
    for relation in table.relations:
        if "," in "".join(relation.column.cells):
            continue  # many related rows: no single value to repeat
        stem = _REF.match(relation.column.header).group(1).lower()  # type: ignore[union-attr]
        header = column.header.lower()
        for other in relation.target.columns:
            if other is relation.target.key:
                continue
            name = other.header.lower()
            if header not in {name, f"{stem}_{name}"} and not (
                other is relation.target.title and header in {f"{stem}_{n}" for n in TITLE_NAMES}
            ):
                continue
            pairs = 0
            for i, cell in enumerate(column.cells):
                ref = relation.column.cells[i].strip()
                if not ref or ref not in relation.target.key_index:
                    continue
                theirs = other.cells[relation.target.key_index[ref]]
                if cell.strip() and not _same(cell, theirs):
                    break
                pairs += 1
            else:
                if pairs:
                    found.append((relation, other))
    return found


def _merge_date_pairs(table: Table) -> None:
    """Fold ``start``/``end`` date columns into one ``start → end`` column."""
    for start_re, end_re in _PAIR_PATTERNS:
        for start in list(table.columns):
            m = start_re.match(start.header)
            if not m or start not in table.columns:
                continue
            stem = (m.group(1) or "") if m.groups() else ""
            end = next(
                (
                    c
                    for c in table.columns
                    if c is not start
                    and (e := end_re.match(c.header))
                    and ((e.group(1) or "") if e.groups() else "").lower() == stem.lower()
                ),
                None,
            )
            if end is None:
                continue
            if not all(parse_date(v) for v in start.cells + end.cells if v.strip()):
                continue
            label = "Dates" if stem.lower() in {"date", "at", "time", ""} else humanize(stem)
            merged = Column(
                header=f"{start.header}__range",
                cells=[
                    f"{s} → {e}" if s and e else s or e
                    for s, e in zip(start.cells, end.cells, strict=True)
                ],
                label=label,
            )
            table.columns[table.columns.index(start)] = merged
            table.columns.remove(end)


def link_tables(nodes: list[Any]) -> int:
    """Wire the tables of database nodes together; returns the number of relations.

    Each node carries ``table`` (header, body) and ``schema`` as the planner read them.
    Nodes that take part get a new ``spec``; with no links found nothing changes."""
    tables = []
    for node in nodes:
        header, body = node.table
        columns = [Column(h, [row[i] for row in body]) for i, h in enumerate(header)]
        tables.append(Table(node=node, columns=columns, rows=len(body)))
    for table in tables:
        _find_key(table)
    for table in tables:
        _find_relations(table, tables)
    linked = [
        t for t in tables if t.relations or any(t is r.target for o in tables for r in o.relations)
    ]
    if not any(t.relations for t in tables):
        return 0
    for table in linked:
        table.node.title = humanize(table.node.title)  # scan_runs -> Scan Runs

    for table in linked:
        for column in table.columns:
            column.label = column.label or humanize(column.header)
    for table in linked:
        # A name-like column is a better title than an id.
        if table.key is not None:
            table.title = next(
                (
                    c
                    for name in TITLE_NAMES
                    for c in table.columns
                    if c.header.lower() == name and c is not table.key
                ),
                table.key,
            )
        else:
            table.title = table.columns[0] if table.columns else None
    # Copies of a related row's name are dropped first (the relation shows the name),
    # then other copies become lookups of a column that is still there.
    copies: dict[int, list[tuple[Relation, Column]]] = {}
    for table in linked:
        for relation in table.relations:
            relation.label = _relation_label(relation)
        for column in table.columns:
            if column in (table.key, table.title) or any(
                r.column is column for r in table.relations
            ):
                continue
            found = _copied_from(table, column)
            copies[id(column)] = found
            if any(other is relation.target.title for relation, other in found):
                table.dropped.append(column)
    for table in linked:
        for column in table.columns:
            if column in table.dropped:
                continue
            for relation, other in copies.get(id(column), []):
                if other not in relation.target.dropped:
                    table.lookups.append((column, relation, other))
                    break
        _merge_date_pairs(table)

    count = 0
    for table in linked:
        _write_spec(table)
        count += len(table.relations)
    return count


def _write_spec(table: Table) -> None:
    node = table.node
    relation_columns = {id(r.column): r for r in table.relations}
    lookup_columns = {id(c): (r, o) for c, r, o in table.lookups}
    dropped = {id(c) for c in table.dropped}
    kept = [
        c
        for c in table.columns
        if id(c) not in relation_columns and id(c) not in lookup_columns and id(c) not in dropped
    ]
    if table.title in kept:
        kept.remove(table.title)
        kept.insert(0, table.title)
    labels: list[str] = []
    for column in kept:
        label = column.label
        while label.lower() in {x.lower() for x in labels}:
            label += " (2)"
        labels.append(label)
        column.label = label

    header = [c.label for c in kept]
    body = [[c.cells[i] for c in kept] for i in range(table.rows)]
    schema = getattr(node, "schema", None)
    spec = table_spec(header, body, schema)
    ranges = {c.label for c in kept if c.header.endswith("__range")}
    for prop in spec["properties"]:
        if prop["name"] in ranges:
            prop["type"] = "date_range"

    taken = {c.label.lower() for c in kept}
    relations = []
    for relation in table.relations:
        label = relation.label
        while label.lower() in taken:
            label += " (2)"
        taken.add(label.lower())
        relation.label = label
        reverse = table.name
        siblings = [r for r in table.relations if r.target is relation.target]
        if len(siblings) > 1:
            reverse = f"{table.name} ({label})"
        relations.append(
            {
                "name": label,
                "target": str(relation.target.node.id),
                "reverse_name": None if relation.target is table else reverse,
                "cells": [c.strip() for c in relation.column.cells],
            }
        )
    lookups = []
    lookup_names: dict[int, str] = {}
    for column, relation, other in table.lookups:
        label = column.label
        while label.lower() in taken:
            label += " (2)"
        taken.add(label.lower())
        lookup_names[id(column)] = label
        lookups.append({"name": label, "relation": relation.label, "target_property": other.label})

    order = [table.title.label] if table.title in kept else []
    for column in table.columns:
        if column is table.title:
            continue
        if column in kept:
            order.append(column.label)
        elif id(column) in relation_columns:
            order.append(relation_columns[id(column)].label)
        elif id(column) in lookup_names:
            order.append(lookup_names[id(column)])

    spec["relations"] = relations
    spec["lookups"] = lookups
    spec["order"] = order
    spec["keys"] = [c.strip() for c in table.key.cells] if table.key else []
    spec["all_rows"] = True  # rows line up with relation cells and keys
    node.spec = spec
