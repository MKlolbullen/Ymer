"""Turning a model's structured answer into Ymer content.

- Mind maps are written as a Yjs update in the mind map storage format (see
  ``frontend/src/mindmap/model.ts``), laid out the way the app's "Tidy" button does.
- Databases are created with the same rules as the databases API: values are cleaned
  per property type, select options are created from the values used.

Model output is treated as untrusted input: every label and value is trimmed, length
limited and type-checked, and anything that does not fit is dropped.
"""

import uuid
from typing import Any

import pycrdt
from sqlalchemy.orm import Session

from ymer.content import MINDMAP_MAP
from ymer.databases import OPTION_COLORS, InvalidValue, clean_value, search_text_for
from ymer.models import (
    DatabaseProperty,
    DatabaseRow,
    DatabaseView,
    Page,
    PropertyType,
    ViewType,
)

H_GAP = 240
V_GAP = 64
BRANCH_COLORS = ["blue", "orange", "aqua", "yellow", "magenta", "green", "violet", "red"]
MAX_NODES = 300
MAX_LABEL = 200


def _label(value: Any, fallback: str = "") -> str:
    text = " ".join(str(value or "").split())[:MAX_LABEL]
    return text or fallback


# --- mind maps ------------------------------------------------------------------------


def mindmap_tree(spec: dict) -> list[dict]:
    """Flatten ``{title, ideas: [{label, children: [...]}]}`` to nodes with parents."""
    nodes = [{"id": "root", "label": _label(spec.get("title"), "Mind map"), "parent": None}]

    def add(items: Any, parent: str, depth: int) -> None:
        if not isinstance(items, list) or depth > 3:
            return
        for item in items:
            if len(nodes) >= MAX_NODES or not isinstance(item, dict):
                return
            label = _label(item.get("label"))
            if not label:
                continue
            node_id = uuid.uuid4().hex[:12]
            nodes.append({"id": node_id, "label": label, "parent": parent})
            add(item.get("children"), node_id, depth + 1)

    add(spec.get("ideas"), "root", 1)
    return nodes


def tidy_layout(nodes: list[dict]) -> dict[str, tuple[float, float]]:
    """Positions like ``tidyLayout`` in the frontend: branches split left and right."""
    kids: dict[str, list[str]] = {}
    for node in nodes:
        if node["parent"]:
            kids.setdefault(node["parent"], []).append(node["id"])
    height: dict[str, float] = {}

    def measure(node_id: str) -> float:
        children = kids.get(node_id, [])
        h = sum(measure(c) for c in children) if children else V_GAP
        height[node_id] = h
        return h

    measure("root")
    out: dict[str, tuple[float, float]] = {"root": (0, 0)}

    def place(node_id: str, x: float, top: float, direction: int) -> None:
        out[node_id] = (x, top + height[node_id] / 2 - V_GAP / 2)
        y = top
        for child in kids.get(node_id, []):
            place(child, x + direction * H_GAP, y, direction)
            y += height[child]

    branches = kids.get("root", [])
    right, left = branches, []
    if len(branches) > 3:
        total = sum(height[b] for b in branches)
        acc, right = 0.0, []
        for b in branches:
            if acc < total / 2:
                right.append(b)
            acc += height[b]
        left = [b for b in branches if b not in right]
    for side, direction in ((right, 1), (left, -1)):
        y = -sum(height[b] for b in side) / 2
        for b in side:
            place(b, direction * H_GAP, y, direction)
            y += height[b]
    return out


def mindmap_state(spec: dict) -> tuple[bytes, int]:
    """A Yjs update holding the mind map, and its number of ideas."""
    nodes = mindmap_tree(spec)
    positions = tidy_layout(nodes)
    doc = pycrdt.Doc()
    root_map = doc.get(MINDMAP_MAP, type=pycrdt.Map)
    branch_index = 0
    with doc.transaction():
        for node in nodes:
            x, y = positions.get(node["id"], (0, 0))
            fields: dict[str, Any] = {
                "label": node["label"],
                "parent": node["parent"],
                "x": float(x),
                "y": float(y),
            }
            if node["parent"] == "root":
                fields["color"] = BRANCH_COLORS[branch_index % len(BRANCH_COLORS)]
                branch_index += 1
            root_map[node["id"]] = pycrdt.Map(fields)
    return doc.get_update(), len(nodes) - 1


# --- databases ------------------------------------------------------------------------

TYPES = {
    "title": PropertyType.TITLE,
    "text": PropertyType.TEXT,
    "number": PropertyType.NUMBER,
    "select": PropertyType.SELECT,
    "multi_select": PropertyType.MULTI_SELECT,
    "date": PropertyType.DATE,
    "checkbox": PropertyType.CHECKBOX,
    "url": PropertyType.URL,
    "person": PropertyType.PERSON,
    "date_range": PropertyType.DATE_RANGE,
    "file": PropertyType.FILE,
}
MAX_PROPERTIES = 12
MAX_ROWS = 100


def _options(names: list[str]) -> list[dict]:
    return [
        {"id": uuid.uuid4().hex[:12], "name": name, "color": OPTION_COLORS[i % len(OPTION_COLORS)]}
        for i, name in enumerate(names)
    ]


def _raw_value(prop: DatabaseProperty, value: str) -> Any:
    """Turn the model's string into what ``clean_value`` expects; adds select options."""
    value = value.strip()
    if not value:
        return None
    match prop.type:
        case PropertyType.CHECKBOX:
            return value.lower() in {"true", "yes", "1", "x", "done", "✓"}
        case PropertyType.SELECT | PropertyType.MULTI_SELECT:
            names = [value] if prop.type is PropertyType.SELECT else value.split(",")
            ids = []
            options = list(prop.config.get("options", []))
            for name in (n.strip()[:100] for n in names):
                if not name:
                    continue
                match = next((o for o in options if o["name"].lower() == name.lower()), None)
                if match is None:
                    if len(options) >= 50:
                        continue
                    match = _options([name])[0]
                    match["color"] = OPTION_COLORS[len(options) % len(OPTION_COLORS)]
                    options.append(match)
                ids.append(match["id"])
            prop.config = {**prop.config, "options": options}
            if not ids:
                return None
            return ids[0] if prop.type is PropertyType.SELECT else ids
        case PropertyType.DATE:
            if len(value) > 10 and value[10] in "T ":
                return value.replace(" ", "T", 1)  # a timestamp; clean_value checks it
            return value[:10] if len(value) >= 10 else value
        case PropertyType.PERSON:
            return [{"name": n.strip()} for n in value.split(",") if n.strip()]
    return value


def add_rows(
    session: Session,
    page: Page,
    props: list[DatabaseProperty],
    rows: list,
    *,
    user_id: uuid.UUID,
    start: float = 1.0,
    keep_empty: bool = False,
) -> list[DatabaseRow]:
    """Add rows given as ``{"values": [{"property": name, "value": text}]}`` to a database,
    after any rows at positions below ``start``. Select options are added as needed."""
    by_name = {p.name.lower(): p for p in props}
    added = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("values"), list):
            continue
        values: dict[str, Any] = {}
        for pair in item["values"]:
            if not isinstance(pair, dict):
                continue
            prop = by_name.get(str(pair.get("property", "")).strip().lower())
            if prop is None or str(prop.id) in values:
                continue
            try:
                raw = _raw_value(prop, str(pair.get("value") or ""))
                cleaned = clean_value(prop, raw, member_ids=set())
            except InvalidValue:
                continue
            if cleaned is not None:
                values[str(prop.id)] = cleaned
        if not values and not keep_empty:
            continue
        row = DatabaseRow(
            workspace_id=page.workspace_id,
            database_id=page.id,
            values=values,
            position=start + len(added),
            search_text=search_text_for(props, values),
            created_by_id=user_id,
            updated_by_id=user_id,
        )
        session.add(row)
        added.append(row)
    return added


def create_database(
    session: Session,
    page: Page,
    spec: dict,
    *,
    user_id: uuid.UUID,
    max_properties: int = MAX_PROPERTIES,
    max_rows: int = MAX_ROWS,
) -> tuple[int, int]:
    """Fill a new, empty database page from ``spec``. Returns (properties, rows).

    Also used by imports and templates (``ymer.importing``), with higher limits."""
    seen: set[str] = set()
    props: list[DatabaseProperty] = []
    raw_props = spec.get("properties") if isinstance(spec.get("properties"), list) else []
    has_title = False
    for item in raw_props:
        if len(props) >= max_properties or not isinstance(item, dict):
            continue
        name = _label(item.get("name"))[:200]
        kind = TYPES.get(str(item.get("type")), PropertyType.TEXT)
        if not name or name.lower() in seen:
            continue
        if kind is PropertyType.TITLE:
            if has_title:
                kind = PropertyType.TEXT
            has_title = True
        seen.add(name.lower())
        config: dict[str, Any] = {}
        if kind in (PropertyType.SELECT, PropertyType.MULTI_SELECT):
            names = list(dict.fromkeys(_label(o)[:100] for o in item.get("options") or []))
            config["options"] = _options([n for n in names if n][:50])
        props.append(
            DatabaseProperty(
                workspace_id=page.workspace_id,
                database_id=page.id,
                name=name,
                type=kind,
                config=config,
                position=len(props),
            )
        )
    if not has_title:
        if props and props[0].type is PropertyType.TEXT:
            props[0].type = PropertyType.TITLE
        else:
            props.insert(
                0,
                DatabaseProperty(
                    workspace_id=page.workspace_id,
                    database_id=page.id,
                    name="Name" if "name" not in seen else "Title",
                    type=PropertyType.TITLE,
                    config={},
                    position=0,
                ),
            )
    props.sort(key=lambda p: (p.type is not PropertyType.TITLE, p.position))
    for i, prop in enumerate(props):
        prop.position = i
        session.add(prop)
    session.flush()

    rows = spec.get("rows") if isinstance(spec.get("rows"), list) else []
    count = len(
        add_rows(
            session,
            page,
            props,
            rows[:max_rows],
            user_id=user_id,
            keep_empty=bool(spec.get("all_rows")),
        )
    )

    session.add(
        DatabaseView(
            workspace_id=page.workspace_id,
            database_id=page.id,
            name="Table",
            type=ViewType.TABLE,
            config={},
            position=0,
        )
    )
    group = next((p for p in props if p.type is PropertyType.SELECT), None)
    if group is not None:
        session.add(
            DatabaseView(
                workspace_id=page.workspace_id,
                database_id=page.id,
                name="Board",
                type=ViewType.BOARD,
                config={"group_by": str(group.id)},
                position=1,
            )
        )
    return len(props), count
