"""The database engine: property values, relations, rollups, filters and sorts.

Storage is described in :mod:`ymer.models.database`. This module holds the rules that
the HTTP layer (``ymer.api.databases``) applies:

- :func:`clean_value` checks and normalises a value written to a property.
- :class:`RowReader` loads rows with their relation and rollup values filled in, for
  one user (relations into a database they cannot see come back without titles, and
  rollups over it come back empty).
- :func:`apply_view` filters and sorts loaded rows by a view's config.

Filtering and sorting run in Python over the loaded rows. That keeps every property
type (including rollups, which are computed) on one code path; it is fine for the few
thousand rows a team database holds and can move into SQL when it needs to.
"""

import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ymer.models import (
    DatabaseProperty,
    DatabaseRelation,
    DatabaseRow,
    DatabaseView,
    Page,
    PageType,
    PropertyType,
    Role,
    User,
    ViewType,
    WorkspaceMember,
)
from ymer.permissions import WorkspaceScope

T = PropertyType

# Types whose value lives in ``database_rows.values``.
STORED_TYPES = {
    T.TITLE,
    T.TEXT,
    T.NUMBER,
    T.SELECT,
    T.MULTI_SELECT,
    T.DATE,
    T.CHECKBOX,
    T.URL,
    T.PERSON,
}
# Types a property can be switched between; values are converted where possible.
CONVERTIBLE_TYPES = STORED_TYPES - {T.TITLE}
TEXT_TYPES = {T.TITLE, T.TEXT, T.URL}
OPTION_COLORS = ["gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"]
ROLLUP_FUNCTIONS = {
    "show_original",
    "count",
    "count_values",
    "count_unique",
    "sum",
    "average",
    "min",
    "max",
    "percent_checked",
}
FILTER_OPS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "is_empty",
    "is_not_empty",
}
MAX_TEXT = 10_000
MAX_TITLE = 2_000


class InvalidValue(ValueError):
    """A value or config the client sent does not fit the property."""


# --- creating a database --------------------------------------------------------------


def create_default_schema(session: Session, page: Page) -> None:
    """Give a new database page its title property and a table view. Caller commits."""
    session.add(
        DatabaseProperty(
            workspace_id=page.workspace_id,
            database_id=page.id,
            name="Name",
            type=T.TITLE,
            position=0,
        )
    )
    session.add(
        DatabaseView(
            workspace_id=page.workspace_id,
            database_id=page.id,
            name="Table",
            type=ViewType.TABLE,
            config={},
        )
    )


def properties_of(session: Session, database_id: uuid.UUID) -> list[DatabaseProperty]:
    return list(
        session.scalars(
            select(DatabaseProperty)
            .where(DatabaseProperty.database_id == database_id)
            .order_by(DatabaseProperty.position, DatabaseProperty.created_at)
        )
    )


def title_property(props: list[DatabaseProperty]) -> DatabaseProperty | None:
    return next((p for p in props if p.type is T.TITLE), None)


def next_row_position(session: Session, database_id: uuid.UUID) -> float:
    top = session.scalar(
        select(func.max(DatabaseRow.position)).where(DatabaseRow.database_id == database_id)
    )
    return (top or 0) + 1


# --- property config ------------------------------------------------------------------


def clean_options(raw: Any) -> list[dict]:
    """Select options: ``[{id?, name, color?}]``; ids are kept or minted, names unique."""
    if not isinstance(raw, list):
        raise InvalidValue("options must be a list.")
    seen: set[str] = set()
    options = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise InvalidValue("Each option needs a name.")
        name = item["name"].strip()[:100]
        if not name or name.lower() in seen:
            raise InvalidValue("Option names must be unique and not blank.")
        seen.add(name.lower())
        color = item.get("color")
        if color not in OPTION_COLORS:
            color = OPTION_COLORS[i % len(OPTION_COLORS)]
        option_id = item.get("id")
        if not isinstance(option_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", option_id):
            option_id = uuid.uuid4().hex[:12]
        options.append({"id": option_id, "name": name, "color": color})
    if len({o["id"] for o in options}) != len(options):
        raise InvalidValue("Option ids must be unique.")
    return options


def option_ids(prop: DatabaseProperty) -> set[str]:
    return {o["id"] for o in prop.config.get("options", [])}


# --- values ---------------------------------------------------------------------------


def _text(raw: Any, limit: int) -> str | None:
    if not isinstance(raw, str):
        raise InvalidValue("Expected text.")
    raw = raw.strip()
    return raw[:limit] or None


def _url(raw: Any) -> str | None:
    value = _text(raw, 2000)
    if value is None:
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*:", value, re.I):
        if not re.match(r"^(https?|mailto):", value, re.I):
            raise InvalidValue("Links must start with http://, https:// or mailto:.")
        return value
    return f"https://{value}"


def _date(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise InvalidValue("Dates are ISO strings (YYYY-MM-DD or a full timestamp).")
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw).isoformat()
        return datetime.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise InvalidValue("Dates are ISO strings (YYYY-MM-DD or a full timestamp).") from exc


def _number(raw: Any) -> float | int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw.replace(",", "."))
        except ValueError as exc:
            raise InvalidValue("Expected a number.") from exc
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise InvalidValue("Expected a number.")
    return int(raw) if float(raw).is_integer() and abs(raw) < 2**53 else float(raw)


def _id_list(raw: Any, allowed: set[str], what: str) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise InvalidValue(f"Expected a list of {what} ids.")
    unknown = [v for v in raw if v not in allowed]
    if unknown:
        raise InvalidValue(f"Unknown {what}: {unknown[0]}.")
    return list(dict.fromkeys(raw)) or None


def clean_value(prop: DatabaseProperty, raw: Any, *, member_ids: set[str]) -> Any:
    """Normalise a value for a stored property; None means "clear". Raises InvalidValue."""
    if raw is None:
        return None
    match prop.type:
        case T.TITLE:
            return _text(raw, MAX_TITLE)
        case T.TEXT:
            return _text(raw, MAX_TEXT)
        case T.URL:
            return _url(raw)
        case T.NUMBER:
            return _number(raw)
        case T.CHECKBOX:
            if not isinstance(raw, bool):
                raise InvalidValue("Expected true or false.")
            return raw or None
        case T.DATE:
            return _date(raw)
        case T.SELECT:
            if not isinstance(raw, str) or raw not in option_ids(prop):
                raise InvalidValue("Unknown option.")
            return raw
        case T.MULTI_SELECT:
            return _id_list(raw, option_ids(prop), "option")
        case T.PERSON:
            return _id_list(raw, member_ids, "person")
    raise InvalidValue(f"{prop.type.value} values cannot be set directly.")


def convert_value(value: Any, old: DatabaseProperty, new_type: PropertyType) -> Any:
    """Best-effort conversion when a property changes type; None when it does not fit."""
    if value is None:
        return None
    names = {o["id"]: o["name"] for o in old.config.get("options", [])}
    if old.type is T.SELECT:
        value = names.get(value)
    elif old.type is T.MULTI_SELECT:
        value = ", ".join(names[v] for v in value if v in names) or None
    elif old.type is T.PERSON:
        return value if new_type is T.PERSON else None
    if value is None:
        return None
    try:
        match new_type:
            case T.TEXT:
                return _text(str(value), MAX_TEXT)
            case T.URL:
                return _url(str(value))
            case T.NUMBER:
                return _number(value if not isinstance(value, bool) else int(value))
            case T.CHECKBOX:
                return value is True or str(value).strip().lower() in {"true", "yes", "1", "x"}
            case T.DATE:
                return _date(str(value))
    except InvalidValue:
        return None
    return None  # select types are rebuilt from text by the caller


def search_text_for(props: list[DatabaseProperty], values: dict) -> str:
    parts = [
        str(values[str(p.id)]) for p in props if p.type in TEXT_TYPES and values.get(str(p.id))
    ]
    return "\n".join(parts)[:100_000]


def workspace_member_ids(session: Session, workspace_id: uuid.UUID) -> set[str]:
    return {
        str(uid)
        for uid in session.scalars(
            select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id)
        )
    }


# --- relations ------------------------------------------------------------------------


def relation_owner_id(prop: DatabaseProperty) -> uuid.UUID:
    """The property whose id the links are stored under (this one, or its other half)."""
    reverse_of = prop.config.get("reverse_of")
    return uuid.UUID(reverse_of) if reverse_of else prop.id


def relation_target_id(prop: DatabaseProperty) -> uuid.UUID | None:
    target = prop.config.get("database_id")
    return uuid.UUID(target) if target else None


def linked_rows(
    session: Session, prop: DatabaseProperty, row_ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Map each row (of ``prop``'s database) to the rows it links to through ``prop``."""
    owner = relation_owner_id(prop)
    reverse = owner != prop.id
    here = DatabaseRelation.to_row_id if reverse else DatabaseRelation.from_row_id
    there = DatabaseRelation.from_row_id if reverse else DatabaseRelation.to_row_id
    stmt = select(here, there).where(DatabaseRelation.property_id == owner)
    if row_ids is not None:
        stmt = stmt.where(here.in_(row_ids))
    if reverse:
        # Links are ordered by whoever set them on the owning side; from this side,
        # show them in the other database's row order instead.
        stmt = stmt.join(DatabaseRow, DatabaseRow.id == there).order_by(
            DatabaseRow.position, DatabaseRow.created_at
        )
    else:
        stmt = stmt.order_by(DatabaseRelation.position)
    result: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for a, b in session.execute(stmt):
        result[a].append(b)
    return result


def set_links(session: Session, prop: DatabaseProperty, row: DatabaseRow, raw: Any) -> None:
    """Replace the links of ``row`` through relation ``prop`` with ``raw`` (row ids)."""
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise InvalidValue("Relation values are lists of row ids.")
    try:
        wanted = list(dict.fromkeys(uuid.UUID(v) for v in raw))
    except ValueError as exc:
        raise InvalidValue("Relation values are lists of row ids.") from exc
    target = relation_target_id(prop)
    if wanted:
        found = set(
            session.scalars(
                select(DatabaseRow.id).where(
                    DatabaseRow.id.in_(wanted), DatabaseRow.database_id == target
                )
            )
        )
        if missing := [str(w) for w in wanted if w not in found]:
            raise InvalidValue(f"Row {missing[0]} is not in the related database.")
    owner = relation_owner_id(prop)
    reverse = owner != prop.id
    here = DatabaseRelation.to_row_id if reverse else DatabaseRelation.from_row_id
    session.query(DatabaseRelation).filter(
        DatabaseRelation.property_id == owner, here == row.id
    ).delete(synchronize_session=False)
    tail: dict[uuid.UUID, int] = {}
    if reverse and wanted:
        # Written from the other side: append to each owning row's own ordering.
        tail = dict(
            session.execute(
                select(DatabaseRelation.from_row_id, func.max(DatabaseRelation.position))
                .where(
                    DatabaseRelation.property_id == owner,
                    DatabaseRelation.from_row_id.in_(wanted),
                )
                .group_by(DatabaseRelation.from_row_id)
            ).all()
        )
    for i, other in enumerate(wanted):
        session.add(
            DatabaseRelation(
                property_id=owner,
                from_row_id=other if reverse else row.id,
                to_row_id=row.id if reverse else other,
                position=tail.get(other, -1) + 1 if reverse else i,
            )
        )


# --- reading rows ---------------------------------------------------------------------


@dataclass
class LoadedRow:
    row: DatabaseRow
    # Property id (str) -> value as the API returns it.
    values: dict[str, Any] = field(default_factory=dict)


class RowReader:
    """Loads a database's rows with relation and rollup values, as one user sees them."""

    def __init__(self, scope: WorkspaceScope, database: Page, props: list[DatabaseProperty]):
        self.scope = scope
        self.session = scope.session
        self.database = database
        self.props = props
        self._props_cache: dict[uuid.UUID, list[DatabaseProperty]] = {database.id: props}
        self._people: dict[str, dict] = {}

    # helpers

    def can_view(self, database_id: uuid.UUID | None) -> bool:
        if database_id is None:
            return False
        page = self.session.scalar(self.scope.pages_query().where(Page.id == database_id))
        if page is None or page.type is not PageType.DATABASE:
            return False
        role = self.scope.page_role(page)
        return role is not None and role.at_least(Role.VIEWER)

    def props_for(self, database_id: uuid.UUID) -> list[DatabaseProperty]:
        if database_id not in self._props_cache:
            self._props_cache[database_id] = properties_of(self.session, database_id)
        return self._props_cache[database_id]

    def _person_refs(self, ids: list[str]) -> list[dict]:
        missing = [i for i in ids if i not in self._people]
        if missing:
            for user in self.session.scalars(select(User).where(User.id.in_(missing))):
                self._people[str(user.id)] = {"id": str(user.id), "display_name": user.display_name}
        return [self._people[i] for i in ids if i in self._people]

    @staticmethod
    def _stored(prop: DatabaseProperty, raw: Any) -> Any:
        """A stored value, minus option ids that were deleted from the property since."""
        if raw is None:
            return None
        if prop.type is T.SELECT:
            return raw if raw in option_ids(prop) else None
        if prop.type is T.MULTI_SELECT:
            known = option_ids(prop)
            return [v for v in raw if v in known] or None
        return raw

    # loading

    def load(self, rows: list[DatabaseRow] | None = None) -> list[LoadedRow]:
        if rows is None:
            rows = list(
                self.session.scalars(
                    select(DatabaseRow)
                    .where(DatabaseRow.database_id == self.database.id)
                    .order_by(DatabaseRow.position, DatabaseRow.created_at)
                )
            )
        loaded = [LoadedRow(row=r) for r in rows]
        if not loaded:
            return loaded
        row_ids = [r.id for r in rows]
        by_id = {lr.row.id: lr for lr in loaded}

        for prop in self.props:
            key = str(prop.id)
            if prop.type in STORED_TYPES:
                for lr in loaded:
                    value = self._stored(prop, lr.row.values.get(key))
                    if prop.type is T.PERSON and value:
                        value = self._person_refs(value)
                    lr.values[key] = value

        relation_links: dict[uuid.UUID, dict[uuid.UUID, list[uuid.UUID]]] = {}
        for prop in self.props:
            if prop.type is T.RELATION:
                links = linked_rows(self.session, prop, row_ids)
                relation_links[prop.id] = links
                target = relation_target_id(prop)
                visible = self.can_view(target)
                titles = self._titles(target, links) if visible else {}
                for row_id, lr in by_id.items():
                    lr.values[str(prop.id)] = [
                        {"id": str(o), "title": titles.get(o) if visible else None}
                        for o in links.get(row_id, [])
                    ] or None

        for prop in self.props:
            if prop.type is T.ROLLUP:
                self._fill_rollup(prop, loaded, relation_links)
        return loaded

    def _titles(
        self, database_id: uuid.UUID | None, links: dict[uuid.UUID, list[uuid.UUID]]
    ) -> dict[uuid.UUID, str]:
        ids = {o for others in links.values() for o in others}
        if not ids or database_id is None:
            return {}
        title = title_property(self.props_for(database_id))
        rows = self.session.scalars(select(DatabaseRow).where(DatabaseRow.id.in_(ids)))
        key = str(title.id) if title else None
        return {r.id: (r.values.get(key) or "") if key else "" for r in rows}

    def _fill_rollup(
        self,
        prop: DatabaseProperty,
        loaded: list[LoadedRow],
        relation_links: dict[uuid.UUID, dict[uuid.UUID, list[uuid.UUID]]],
    ) -> None:
        key = str(prop.id)
        for lr in loaded:
            lr.values[key] = None
        config = prop.config
        try:
            relation_id = uuid.UUID(config.get("relation_property_id", ""))
            target_prop_id = uuid.UUID(config.get("target_property_id", ""))
        except ValueError:
            return
        relation = next(
            (p for p in self.props if p.id == relation_id and p.type is T.RELATION), None
        )
        if relation is None or relation_id not in relation_links:
            return
        target_db = relation_target_id(relation)
        if not self.can_view(target_db):
            return
        target = next((p for p in self.props_for(target_db) if p.id == target_prop_id), None)
        if target is None or target.type is T.ROLLUP:
            return
        function = config.get("function", "count")
        links = relation_links[relation_id]
        related_ids = list({o for others in links.values() for o in others})
        related = {
            r.id: r
            for r in self.session.scalars(
                select(DatabaseRow).where(DatabaseRow.id.in_(related_ids))
            )
        }
        nested = linked_rows(self.session, target, related_ids) if target.type is T.RELATION else {}
        names = {o["id"]: o["name"] for o in target.config.get("options", [])}

        def display(row_id: uuid.UUID) -> list[Any]:
            """The target values of one related row, flattened for aggregation."""
            if target.type is T.RELATION:
                return [str(o) for o in nested.get(row_id, [])]
            row = related.get(row_id)
            raw = self._stored(target, row.values.get(str(target.id))) if row else None
            if raw is None:
                return []
            if target.type is T.SELECT:
                return [names[raw]]
            if target.type is T.MULTI_SELECT:
                return [names[v] for v in raw]
            if target.type is T.PERSON:
                return [p["display_name"] for p in self._person_refs(raw)]
            return [raw]

        for lr in loaded:
            others = links.get(lr.row.id, [])
            values = [v for o in others for v in display(o)]
            lr.values[key] = rollup(function, values, len(others))


def rollup(function: str, values: list[Any], related_count: int) -> Any:
    numbers = [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
    match function:
        case "show_original":
            return values or None
        case "count":
            return related_count
        case "count_values":
            return len(values)
        case "count_unique":
            return len({str(v) for v in values})
        case "sum":
            return _tidy(sum(numbers)) if numbers else None
        case "average":
            return _tidy(sum(numbers) / len(numbers)) if numbers else None
        case "min" | "max":
            pick = min if function == "min" else max
            if numbers:
                return _tidy(pick(numbers))
            strings = [v for v in values if isinstance(v, str)]
            return pick(strings) if strings else None  # dates compare as ISO strings
        case "percent_checked":
            if not related_count:
                return None
            checked = sum(1 for v in values if v is True)
            return _tidy(round(100 * checked / related_count, 1))
    return None


def _tidy(n: float) -> float | int:
    return int(n) if float(n).is_integer() else round(n, 6)


# --- views: filters and sorts ---------------------------------------------------------


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value is False


def _ids(value: Any) -> list[str]:
    """Comparable ids of a list value (options, people, related rows)."""
    if not isinstance(value, list):
        return []
    return [v["id"] if isinstance(v, dict) else str(v) for v in value]


def _matches(prop: DatabaseProperty, value: Any, op: str, target: Any) -> bool:
    if op == "is_empty":
        return _is_empty(value)
    if op == "is_not_empty":
        return not _is_empty(value)
    if target is None or target == "":
        return True  # a filter still being filled in does not hide anything
    t = prop.type
    if t is T.CHECKBOX:
        want = target is True or str(target).lower() == "true"
        return (value is True) == want if op == "equals" else (value is True) != want
    if t in {T.MULTI_SELECT, T.PERSON, T.RELATION}:
        has = str(target) in _ids(value)
        if op in {"contains", "equals"}:
            return has
        if op in {"not_contains", "not_equals"}:
            return not has
        return True
    if t is T.ROLLUP and isinstance(value, list):
        text = " ".join(str(v) for v in value).lower()
        has = str(target).lower() in text
        return has if op in {"contains", "equals"} else not has if op.startswith("not") else True
    if op in {"gt", "gte", "lt", "lte"}:
        if value is None:
            return False
        try:
            left, right = (
                (float(value), float(target))
                if t in {T.NUMBER, T.ROLLUP}
                else (str(value)[:10], str(target)[:10])
            )
        except (TypeError, ValueError):
            return False
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[op]
    if t is T.NUMBER or (t is T.ROLLUP and isinstance(value, int | float)):
        try:
            same = value is not None and float(value) == float(target)
        except (TypeError, ValueError):
            same = False
    elif t is T.DATE:
        same = value is not None and str(value)[:10] == str(target)[:10]
    else:
        left, right = str(value or "").lower(), str(target).lower()
        if op in {"contains", "not_contains"}:
            return (right in left) == (op == "contains")
        same = left == right
    if op in {"equals", "contains"}:
        return same
    if op in {"not_equals", "not_contains"}:
        return not same
    return True


def _sort_key(prop: DatabaseProperty, value: Any) -> tuple:
    """(is_empty, key): empty values always sort last."""
    if _is_empty(value) and prop.type is not T.CHECKBOX:
        return (1, 0)
    t = prop.type
    if t is T.CHECKBOX:
        return (0, 1 if value else 0)
    if t is T.NUMBER:
        return (0, float(value))
    if t is T.SELECT:
        order = [o["id"] for o in prop.config.get("options", [])]
        return (0, order.index(value) if value in order else len(order))
    if t is T.MULTI_SELECT:
        order = [o["id"] for o in prop.config.get("options", [])]
        return (0, min((order.index(v) for v in value if v in order), default=len(order)))
    if t in {T.PERSON, T.RELATION}:
        return (0, ", ".join(str(v.get("display_name") or v.get("title") or "") for v in value))
    if t is T.ROLLUP:
        if isinstance(value, int | float):
            return (0, float(value))
        return (0, str(value).lower())
    return (0, str(value).lower())


def apply_view(
    props: list[DatabaseProperty], rows: list[LoadedRow], config: dict
) -> list[LoadedRow]:
    """Filter (all conditions must hold) then sort rows by a view config."""
    by_id = {str(p.id): p for p in props}
    for flt in config.get("filters") or []:
        prop = by_id.get(str(flt.get("property_id")))
        op = flt.get("op")
        if prop is None or op not in FILTER_OPS:
            continue  # filters on deleted properties are ignored
        rows = [r for r in rows if _matches(prop, r.values.get(str(prop.id)), op, flt.get("value"))]
    # Python's sort is stable: apply sorts from the least to the most significant.
    for srt in reversed(config.get("sorts") or []):
        prop = by_id.get(str(srt.get("property_id")))
        if prop is None:
            continue
        descending = srt.get("direction") == "desc"
        keyed = [(_sort_key(prop, r.values.get(str(prop.id))), r) for r in rows]
        filled = [(k[1], r) for k, r in keyed if k[0] == 0]
        empty = [r for k, r in keyed if k[0] == 1]
        filled.sort(key=lambda pair: pair[0], reverse=descending)
        rows = [r for _, r in filled] + empty
    return rows
