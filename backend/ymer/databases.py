"""The database engine: property values, relations, rollups, formulas, filters and sorts.

Storage is described in :mod:`ymer.models.database`. This module holds the rules that
the HTTP layer (``ymer.api.databases``) applies:

- :func:`clean_value` checks and normalises a value written to a property.
- :class:`Engine` computes columns for one user: stored values, relations, rollups and
  formulas, across databases. Computed properties can build on each other (a rollup of
  a formula of a lookup), so columns are computed on demand and remembered for the
  request; a loop of properties that depend on each other comes back empty.
- :class:`RowReader` loads one database's rows with every value filled in (relations
  into a database the user cannot see come back without titles, and rollups over it
  come back empty).
- :func:`apply_view` filters and sorts loaded rows by a view's config.

Rollups follow one relation, or two (``through_property_id``: Students → Enrollments →
Courses), can filter the rows they reach at each step (``filters``, ``through_filters``),
and a filter can compare with the row being computed (``{"this": <property id>}``: the
grades of *this* enrollment's student). Formulas are in :mod:`ymer.formulas`.

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

from ymer.formulas import FormulaError, compile_formula, evaluate, references
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
    T.DATE_RANGE,
    T.FILE,
}
COMPUTED_TYPES = {T.ROLLUP, T.FORMULA}
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
    "median",
    "weighted_average",
    "top_option",
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
    "in",
    "not_in",
}
MAX_TEXT = 10_000
MAX_TITLE = 2_000
MAX_FILES = 50
FILE_LINK = re.compile(r"^(https?|s3|gs|minio|ftp|sftp)://\S+$", re.IGNORECASE)


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


def _people(raw: Any, member_ids: set[str], member_names: dict[str, str]) -> list | None:
    """People are member ids, or ``{"name": …}`` for someone who is not (yet) a member.

    A name that matches a member's display name is stored as that member; others stay
    names and are matched again whenever the rows are read (:class:`Engine`)."""
    if not isinstance(raw, list):
        raise InvalidValue("Expected a list of people.")
    out: list = []
    for item in raw:
        if isinstance(item, dict):
            if isinstance(item.get("id"), str):
                item = item["id"]
            elif isinstance(item.get("name"), str):
                name = " ".join(item["name"].split())[:200]
                if not name:
                    continue
                matched = member_names.get(name.lower())
                if matched:
                    if matched not in out:
                        out.append(matched)
                    continue
                item = {"name": name}
            else:
                raise InvalidValue('Each person is a member id or {"name": …}.')
        if isinstance(item, str):
            if item not in member_ids:
                raise InvalidValue(f"Unknown person: {item}.")
        elif not isinstance(item, dict):
            raise InvalidValue('Each person is a member id or {"name": …}.')
        if item not in out:
            out.append(item)
    return out[:MAX_FILES] or None


def _date_range(raw: Any) -> dict | None:
    if isinstance(raw, str):
        parts = re.split(r"\s*(?:→|/|\.\.)\s*", raw.strip(), maxsplit=1)
        raw = {"start": parts[0], "end": parts[1] if len(parts) > 1 else None}
    if not isinstance(raw, dict):
        raise InvalidValue('Date ranges are {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}.')
    start, end = _date(raw.get("start")), _date(raw.get("end"))
    if start is None and end is None:
        return None
    if start is None:
        start, end = end, None
    if end is not None and end[:10] < start[:10]:  # type: ignore[index]
        raise InvalidValue("A date range cannot end before it starts.")
    return {"start": start, "end": end}


def _files(raw: Any, page_files: dict[str, str] | None) -> list | None:
    """Files are links (``{"name", "url"}``, any http(s)/s3/gs path) or files uploaded
    to the database page (``{"file_id"}``)."""
    if isinstance(raw, str):
        raw = [{"url": part} for part in re.split(r"[\s,]+", raw.strip()) if part]
    if not isinstance(raw, list):
        raise InvalidValue("Expected a list of files.")
    out = []
    for item in raw:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            raise InvalidValue('Each file is {"url"} or {"file_id"}.')
        if item.get("file_id") is not None:
            file_id = str(item["file_id"])
            if page_files is None or file_id not in page_files:
                raise InvalidValue("That file is not attached to this database.")
            out.append({"file_id": file_id, "name": page_files[file_id]})
            continue
        url = item.get("url")
        if not isinstance(url, str) or not FILE_LINK.match(url.strip()):
            raise InvalidValue("File links start with http(s)://, s3://, gs:// or ftp://.")
        url = url.strip()[:2000]
        name = item.get("name") if isinstance(item.get("name"), str) else None
        name = (name or "").strip()[:255] or file_name(url)
        out.append({"name": name, "url": url})
    return out[:MAX_FILES] or None


def file_name(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return (tail.split("?", 1)[0] or url)[:255]


def clean_value(
    prop: DatabaseProperty,
    raw: Any,
    *,
    member_ids: set[str],
    member_names: dict[str, str] | None = None,
    page_files: dict[str, str] | None = None,
) -> Any:
    """Normalise a value for a stored property; None means "clear". Raises InvalidValue.

    ``member_names`` (lowercase display name -> id) lets people be given by name;
    ``page_files`` (id -> filename) are the files a file value may point at."""
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
            return _people(raw, member_ids, member_names or {})
        case T.DATE_RANGE:
            return _date_range(raw)
        case T.FILE:
            return _files(raw, page_files)
    raise InvalidValue(f"{prop.type.value} values cannot be set directly.")


def convert_value(
    value: Any,
    old: DatabaseProperty,
    new_type: PropertyType,
    *,
    people: dict[str, str] | None = None,
    member_names: dict[str, str] | None = None,
) -> Any:
    """Best-effort conversion when a property changes type; None when it does not fit.

    Text becomes people by name (matched to members where a name fits), and people
    become their names; ``people`` maps member ids to display names for that."""
    if value is None:
        return None
    names = {o["id"]: o["name"] for o in old.config.get("options", [])}
    if old.type is T.SELECT:
        value = names.get(value)
    elif old.type is T.MULTI_SELECT:
        value = ", ".join(names[v] for v in value if v in names) or None
    elif old.type is T.PERSON:
        if new_type is T.PERSON:
            return value
        names = [p["name"] for p in value if isinstance(p, dict) and p.get("name")]
        names += [people[p] for p in value if isinstance(p, str) and p in (people or {})]
        value = ", ".join(names) or None
    elif old.type is T.DATE_RANGE:
        if new_type is T.DATE:
            return value.get("start")
        value = " → ".join(v for v in (value.get("start"), value.get("end")) if v) or None
    elif old.type is T.FILE:
        value = ", ".join(f.get("url") or f.get("name") or "" for f in value) or None
    if value is None:
        return None
    try:
        match new_type:
            case T.PERSON:
                names = [n.strip() for n in str(value).split(",") if n.strip()]
                return _people([{"name": n} for n in names], set(), member_names or {})
            case T.DATE_RANGE:
                return _date_range(str(value))
            case T.FILE:
                return _files(str(value), None)
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


def workspace_members(session: Session, workspace_id: uuid.UUID) -> dict[str, str]:
    """Member id -> display name."""
    return {
        str(uid): name or ""
        for uid, name in session.execute(
            select(User.id, User.display_name)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(WorkspaceMember.workspace_id == workspace_id)
        )
    }


def names_to_members(members: dict[str, str]) -> dict[str, str]:
    """Lowercase display name -> member id, for names that only one member has."""
    seen: dict[str, list[str]] = defaultdict(list)
    for uid, name in members.items():
        if name.strip():
            seen[" ".join(name.split()).lower()].append(uid)
    return {name: ids[0] for name, ids in seen.items() if len(ids) == 1}


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


class Engine:
    """Computes property columns (row id -> value) for one user, across databases.

    Everything a request touches is loaded once: a database's rows, a relation's links,
    a column's values. Values come in two shapes:

    - :meth:`column`: as the API returns them (select option ids, ``[{id, title}]`` for
      relations, ``[{id, display_name}]`` for people). Filters work on these.
    - :meth:`plain_column`: what formulas and rollups compute with (option names, related
      titles, people's names, ``True``/``None`` for checkboxes).
    """

    def __init__(self, scope: WorkspaceScope):
        self.scope = scope
        self.session: Session = scope.session
        self._props: dict[uuid.UUID, list[DatabaseProperty]] = {}
        self._rows: dict[uuid.UUID, list[DatabaseRow]] = {}
        self._links: dict[uuid.UUID, dict[uuid.UUID, list[uuid.UUID]]] = {}
        self._columns: dict[uuid.UUID, dict[uuid.UUID, Any]] = {}
        self._plain: dict[uuid.UUID, dict[uuid.UUID, Any]] = {}
        self._active: set[uuid.UUID] = set()
        self._visible: dict[uuid.UUID, bool] = {}
        self._users: dict[str, str] = {}
        self._members: dict[str, str] | None = None

    # schema and rows

    def props(self, database_id: uuid.UUID | None) -> list[DatabaseProperty]:
        if database_id is None:
            return []
        if database_id not in self._props:
            self._props[database_id] = properties_of(self.session, database_id)
        return self._props[database_id]

    def use_props(self, database_id: uuid.UUID, props: list[DatabaseProperty]) -> None:
        self._props[database_id] = props

    def prop(self, database_id: uuid.UUID | None, prop_id: Any) -> DatabaseProperty | None:
        try:
            wanted = uuid.UUID(str(prop_id))
        except ValueError:
            return None
        return next((p for p in self.props(database_id) if p.id == wanted), None)

    def rows(self, database_id: uuid.UUID) -> list[DatabaseRow]:
        if database_id not in self._rows:
            self._rows[database_id] = list(
                self.session.scalars(
                    select(DatabaseRow)
                    .where(DatabaseRow.database_id == database_id)
                    .order_by(DatabaseRow.position, DatabaseRow.created_at)
                )
            )
        return self._rows[database_id]

    def can_view(self, database_id: uuid.UUID | None) -> bool:
        if database_id is None:
            return False
        if database_id not in self._visible:
            page = self.session.scalar(self.scope.pages_query().where(Page.id == database_id))
            visible = False
            if page is not None and page.type is PageType.DATABASE:
                role = self.scope.page_role(page)
                visible = role is not None and role.at_least(Role.VIEWER)
            self._visible[database_id] = visible
        return self._visible[database_id]

    def links(self, prop: DatabaseProperty) -> dict[uuid.UUID, list[uuid.UUID]]:
        if prop.id not in self._links:
            self._links[prop.id] = linked_rows(self.session, prop)
        return self._links[prop.id]

    # people

    def members(self) -> dict[str, str]:
        if self._members is None:
            self._members = workspace_members(self.session, self.scope.workspace.id)
        return self._members

    def _person_refs(self, raw: list) -> list[dict]:
        ids = [p for p in raw if isinstance(p, str)]
        missing = [i for i in ids if i not in self._users]
        if missing:
            for user in self.session.scalars(select(User).where(User.id.in_(missing))):
                self._users[str(user.id)] = user.display_name
        by_name = names_to_members(self.members())
        refs: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                if item in self._users:
                    refs.append({"id": item, "display_name": self._users[item]})
                continue
            name = str(item.get("name") or "")
            matched = by_name.get(" ".join(name.split()).lower())
            if matched:
                refs.append({"id": matched, "display_name": self.members()[matched]})
            elif name:
                refs.append({"id": None, "display_name": name})
        unique: list[dict] = []
        for ref in refs:
            if ref not in unique:
                unique.append(ref)
        return unique

    # columns

    def column(self, prop: DatabaseProperty) -> dict[uuid.UUID, Any]:
        """Row id -> value as the API returns it, for every row of the property's database."""
        if prop.id in self._columns:
            return self._columns[prop.id]
        if prop.id in self._active:
            return {}  # a loop of computed properties: the inner reference is empty
        self._active.add(prop.id)
        try:
            values = self._compute(prop)
        finally:
            self._active.discard(prop.id)
        self._columns[prop.id] = values
        return values

    def _compute(self, prop: DatabaseProperty) -> dict[uuid.UUID, Any]:
        rows = self.rows(prop.database_id)
        key = str(prop.id)
        if prop.type in STORED_TYPES:
            out = {}
            for row in rows:
                value = _stored(prop, row.values.get(key))
                if prop.type is T.PERSON and value:
                    value = self._person_refs(value) or None
                out[row.id] = value
            return out
        if prop.type is T.RELATION:
            links = self.links(prop)
            target = relation_target_id(prop)
            titles = self.titles(target) if self.can_view(target) else None
            return {
                row.id: [
                    {"id": str(o), "title": titles.get(o, "") if titles is not None else None}
                    for o in links.get(row.id, [])
                ]
                or None
                for row in rows
            }
        if prop.type is T.ROLLUP:
            return self._rollup(prop)
        if prop.type is T.FORMULA:
            return self._formula(prop)
        return {}

    def titles(self, database_id: uuid.UUID | None) -> dict[uuid.UUID, str]:
        title = title_property(self.props(database_id))
        if title is None or database_id is None:
            return {r.id: "" for r in self.rows(database_id)} if database_id else {}
        return {row_id: value or "" for row_id, value in self.column(title).items()}

    def plain_column(self, prop: DatabaseProperty) -> dict[uuid.UUID, Any]:
        """Row id -> the value formulas and rollups compute with."""
        if prop.id in self._plain:
            return self._plain[prop.id]
        names = {o["id"]: o["name"] for o in prop.config.get("options", [])}
        out = {}
        for row_id, value in self.column(prop).items():
            out[row_id] = plain_value(prop, value, names)
        if prop.id not in self._active:
            self._plain[prop.id] = out
        return out

    # rollups

    def _rollup(self, prop: DatabaseProperty) -> dict[uuid.UUID, Any]:
        rows = self.rows(prop.database_id)
        empty = {row.id: None for row in rows}
        config = prop.config
        relation = self.prop(prop.database_id, config.get("relation_property_id"))
        if relation is None or relation.type is not T.RELATION:
            return empty
        first_db = relation_target_id(relation)
        if not self.can_view(first_db):
            return empty
        steps = [(relation, first_db, config.get("filters") or [])]
        last_db = first_db
        if config.get("through_property_id"):
            through = self.prop(first_db, config.get("through_property_id"))
            if through is None or through.type is not T.RELATION:
                return empty
            last_db = relation_target_id(through)
            if not self.can_view(last_db):
                return empty
            steps.append((through, last_db, config.get("through_filters") or []))
        target = self.prop(last_db, config.get("target_property_id"))
        if target is None:
            return empty
        function = config.get("function", "count")
        weight = (
            self.prop(last_db, config.get("weight_property_id"))
            if function == "weighted_average"
            else None
        )
        if function == "weighted_average" and weight is None:
            return empty

        checks = [
            (self.links(rel), [self._condition(prop.database_id, db, f) for f in flts])
            for rel, db, flts in steps
        ]
        if any(c is None for _, conds in checks for c in conds):
            return empty  # a filter on a property that no longer exists
        values_of = self.plain_column(target)
        weights_of = self.plain_column(weight) if weight is not None else {}
        options = [o["name"] for o in target.config.get("options", [])]

        out = {}
        for row in rows:
            reached = [row.id]
            for links, conditions in checks:
                reached = [
                    other
                    for here in reached
                    for other in links.get(here, [])
                    if all(cond(row.id, other) for cond in conditions)  # type: ignore[misc]
                ]
            values: list[Any] = []
            pairs: list[tuple[Any, Any]] = []
            for other in reached:
                flat = _flatten(values_of.get(other))
                values.extend(flat)
                if weight is not None:
                    pairs.append((_one(values_of.get(other)), _one(weights_of.get(other))))
            out[row.id] = rollup(function, values, len(reached), pairs=pairs, options=options)
        return out

    def _condition(self, here_db: uuid.UUID, there_db: uuid.UUID | None, flt: Any):
        """A test ``(this row id, related row id) -> bool`` for one rollup filter."""
        if not isinstance(flt, dict):
            return None
        prop = self.prop(there_db, flt.get("property_id"))
        op = flt.get("op")
        if prop is None or op not in FILTER_OPS:
            return None
        column = self.column(prop)
        value = flt.get("value")
        if isinstance(value, dict) and "this" in value:
            mine = self.prop(here_db, value.get("this"))
            if mine is None:
                return None
            own = self.column(mine)

            def compare(this_id: uuid.UUID, other: uuid.UUID) -> bool:
                wanted = _comparable(own.get(this_id))
                if wanted in (None, []):
                    # Nothing to compare with: only "is not" style filters pass.
                    return op in {"not_equals", "not_contains", "not_in"}
                return _matches(prop, column.get(other), op, wanted)

            return compare
        cache: dict[uuid.UUID, bool] = {}

        def check(_this_id: uuid.UUID, other: uuid.UUID) -> bool:
            if other not in cache:
                cache[other] = _matches(prop, column.get(other), op, value)
            return cache[other]

        return check

    # formulas

    def _formula(self, prop: DatabaseProperty) -> dict[uuid.UUID, Any]:
        rows = self.rows(prop.database_id)
        try:
            tree = compile_formula(str(prop.config.get("expression") or ""))
        except FormulaError:
            return {row.id: None for row in rows}
        props = self.props(prop.database_id)
        by_name = {p.name: p for p in props}
        by_lower = {p.name.lower(): p for p in props}
        columns: dict[str, dict[uuid.UUID, Any]] = {}
        for name in references(tree):
            ref = by_name.get(name) or by_lower.get(name.lower())
            columns[name] = self.plain_column(ref) if ref is not None else {}
        out = {}
        for row in rows:
            out[row.id] = evaluate(tree, lambda name, rid=row.id: columns[name].get(rid))
        return out


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


def plain_value(prop: DatabaseProperty, value: Any, names: dict[str, str] | None = None) -> Any:
    """An API value as formulas and rollups see it."""
    if value is None:
        return None
    if names is None:
        names = {o["id"]: o["name"] for o in prop.config.get("options", [])}
    match prop.type:
        case T.SELECT:
            return names.get(value)
        case T.MULTI_SELECT:
            return [names[v] for v in value if v in names] or None
        case T.PERSON:
            return [p["display_name"] for p in value] or None
        case T.RELATION:
            return [r["title"] if r["title"] is not None else r["id"] for r in value] or None
        case T.FILE:
            return [f.get("name") or f.get("url") for f in value] or None
        case T.CHECKBOX:
            return True if value else None
    return value


def _flatten(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [v for item in value for v in _flatten(item)]
    return [value]


def _one(value: Any) -> Any:
    flat = _flatten(value)
    return flat[0] if len(flat) == 1 else None


def _comparable(value: Any) -> Any:
    """This row's value, for a rollup filter that compares with it."""
    if isinstance(value, list):
        return [v.get("id") or v.get("display_name") if isinstance(v, dict) else v for v in value]
    return value


class RowReader:
    """Loads a database's rows with every value filled in, as one user sees them."""

    def __init__(
        self,
        scope: WorkspaceScope,
        database: Page,
        props: list[DatabaseProperty],
        engine: Engine | None = None,
    ):
        self.scope = scope
        self.session = scope.session
        self.database = database
        self.props = props
        self.engine = engine or Engine(scope)
        self.engine.use_props(database.id, props)

    def can_view(self, database_id: uuid.UUID | None) -> bool:
        return self.engine.can_view(database_id)

    def props_for(self, database_id: uuid.UUID) -> list[DatabaseProperty]:
        return self.engine.props(database_id)

    def load(self, rows: list[DatabaseRow] | None = None) -> list[LoadedRow]:
        if rows is None:
            rows = self.engine.rows(self.database.id)
        loaded = [LoadedRow(row=r) for r in rows]
        if not loaded:
            return loaded
        known = {r.id for r in self.engine.rows(self.database.id)}
        if any(r.id not in known for r in rows):
            self.engine._rows.pop(self.database.id, None)  # rows added since: reload
        for prop in self.props:
            column = self.engine.column(prop)
            key = str(prop.id)
            for lr in loaded:
                lr.values[key] = column.get(lr.row.id)
        return loaded


def rollup(
    function: str,
    values: list[Any],
    related_count: int,
    *,
    pairs: list[tuple[Any, Any]] | None = None,
    options: list[str] | None = None,
) -> Any:
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
            return _tidy(sum(numbers))  # nothing to add up is 0, like a spreadsheet
        case "average":
            return _tidy(sum(numbers) / len(numbers)) if numbers else None
        case "median":
            if not numbers:
                return None
            ordered = sorted(numbers)
            mid = len(ordered) // 2
            if len(ordered) % 2:
                return _tidy(ordered[mid])
            return _tidy((ordered[mid - 1] + ordered[mid]) / 2)
        case "weighted_average":
            usable = [
                (float(v), float(w))
                for v, w in pairs or []
                if isinstance(v, int | float)
                and isinstance(w, int | float)
                and not isinstance(v, bool)
                and not isinstance(w, bool)
            ]
            total = sum(w for _, w in usable)
            if not usable or total == 0:
                return None
            return _tidy(sum(v * w for v, w in usable) / total)
        case "top_option":
            order = {name: i for i, name in enumerate(options or [])}
            ranked = [v for v in values if v in order]
            return min(ranked, key=order.__getitem__) if ranked else None
        case "min" | "max":
            pick = min if function == "min" else max
            if numbers:
                return _tidy(pick(numbers))
            strings = [v for v in values if isinstance(v, str)]
            starts = [v["start"] for v in values if isinstance(v, dict) and v.get("start")]
            ends = [
                v.get("end") or v["start"] for v in values if isinstance(v, dict) and v.get("start")
            ]
            strings += starts if function == "min" else ends
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
    """Comparable ids of a list value (options, people, related rows, files)."""
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, dict):
            out.extend(
                str(v[k]).lower() if k == "display_name" else str(v[k])
                for k in ("id", "display_name", "name", "url")
                if v.get(k)
            )
        else:
            out.append(str(v))
    return out


def _option_name(prop: DatabaseProperty, option_id: Any) -> str:
    for option in prop.config.get("options", []):
        if option["id"] == option_id:
            return option["name"].lower()
    return ""


def _matches(prop: DatabaseProperty, value: Any, op: str, target: Any) -> bool:
    if op == "is_empty":
        return _is_empty(value)
    if op == "is_not_empty":
        return not _is_empty(value)
    if target is None or target == "" or target == []:
        return True  # a filter still being filled in does not hide anything
    if op in {"in", "not_in"} or isinstance(target, list):
        # "is any of" / "is none of"; a list also compares with each of its values.
        targets = target if isinstance(target, list) else [target]
        positive = op in {"in", "equals", "contains"}
        single = "contains" if op in {"contains", "not_contains"} else "equals"
        hit = any(_matches(prop, value, single, t) for t in targets)
        return hit if positive else not hit
    t = prop.type
    if t is T.SELECT:
        same = value is not None and (
            str(value) == str(target) or _option_name(prop, value) == str(target).lower()
        )
        if op in {"contains", "not_contains"} and value is not None:
            same = str(target).lower() in _option_name(prop, value)
        return same if op in {"equals", "contains"} else not same
    if t is T.MULTI_SELECT:
        ids = value if isinstance(value, list) else []
        has = str(target) in ids or str(target).lower() in {_option_name(prop, v) for v in ids}
        return has if op in {"contains", "equals"} else not has
    if t is T.DATE_RANGE:
        if not isinstance(value, dict):
            return op.startswith("not")
        start, end = str(value.get("start"))[:10], str(value.get("end") or value.get("start"))[:10]
        day = str(target)[:10]
        if op in {"equals", "contains", "not_equals", "not_contains"}:
            inside = start <= day <= end  # equals: the day falls within the range
            return inside if op in {"equals", "contains"} else not inside
        value = start if op in {"gt", "gte"} else end
        t = T.DATE
    if t is T.CHECKBOX:
        want = target is True or str(target).lower() == "true"
        return (value is True) == want if op == "equals" else (value is True) != want
    if t in {T.PERSON, T.RELATION, T.FILE}:
        ids = _ids(value)
        has = str(target) in ids or str(target).lower() in ids
        if t is T.FILE and op in {"contains", "not_contains"}:
            has = any(str(target).lower() in i.lower() for i in ids)
        if op in {"contains", "equals"}:
            return has
        if op in {"not_contains", "not_equals"}:
            return not has
        return True
    if t in COMPUTED_TYPES and isinstance(value, list):
        text = " ".join(str(v) for v in value).lower()
        has = str(target).lower() in text
        return has if op in {"contains", "equals"} else not has if op.startswith("not") else True
    if op in {"gt", "gte", "lt", "lte"}:
        if value is None:
            return False
        try:
            left, right = (
                (float(value), float(target))
                if t is T.NUMBER or (t in COMPUTED_TYPES and _is_number(value))
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
    if t in COMPUTED_TYPES and isinstance(value, bool):
        same = str(value).lower() == str(target).lower()
    elif t is T.NUMBER or (t in COMPUTED_TYPES and _is_number(value)):
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


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


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
    if t in {T.PERSON, T.RELATION, T.FILE}:
        return (
            0,
            ", ".join(
                str(v.get("display_name") or v.get("title") or v.get("name") or "").lower()
                for v in value
            ),
        )
    if t is T.DATE_RANGE:
        return (0, str(value.get("start") or "") + str(value.get("end") or ""))
    if t in COMPUTED_TYPES:
        # Numbers before text, so a column of mixed results still sorts sensibly.
        if _is_number(value) or isinstance(value, bool):
            return (0, (0, float(value), ""))
        return (0, (1, 0.0, str(value).lower()))
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
