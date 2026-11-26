"""Databases: properties, rows and views of a page of type ``database``.

All routes are under ``/api/workspaces/<ws>/databases/<page_id>``. Reading needs the
viewer role on the database page, changing anything needs editor.

- ``GET    ``                        properties and views
- ``GET    /rows[?view_id=&q=]``     rows with values; a view's filters and sorts applied
- ``POST   /rows``                   {values?, position?}
- ``PATCH  /rows/<row_id>``          {values?, position?}; ``values`` is merged, null clears
- ``DELETE /rows/<row_id>``
- ``POST   /properties``             {name, type, config?, two_way?, reverse_name?}
- ``PATCH  /properties/<prop_id>``   {name?, type?, config?, position?}
- ``DELETE /properties/<prop_id>``   the title property cannot be deleted
- ``POST   /views``                  {name, type, config?}
- ``PATCH  /views/<view_id>``        {name?, type?, config?, position?}
- ``DELETE /views/<view_id>``        a database keeps at least one view
- ``POST   /chart``                  {config, view_id?} -> chart data (read-only query)

Values are keyed by property id. A relation value is written as a list of row ids and
read back as ``[{id, title}]``; ``title`` is null when the related database is hidden
from the reader. People are written as member ids or ``{"name"}`` and read back as
``[{id, display_name}]`` (``id`` null for a name no member has). Files are
``[{name, url}]`` links or ``[{file_id}]`` files uploaded to the database page. Date ranges
are ``{start, end}``. Rollups and formulas are read-only. See :mod:`ymer.databases`.
"""

import uuid
from typing import Any, Literal

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from ymer.ai.indexing import mark_dirty
from ymer.api.validation import InvalidBody, parse_body
from ymer.api.workspaces import scope_for
from ymer.auth import current_user, login_required
from ymer.charts import ChartConfig, ChartError, chart_data
from ymer.databases import (
    CONVERTIBLE_TYPES,
    FILTER_OPS,
    ROLLUP_FUNCTIONS,
    STORED_TYPES,
    InvalidValue,
    LoadedRow,
    RowReader,
    apply_view,
    clean_options,
    clean_value,
    convert_value,
    names_to_members,
    next_row_position,
    properties_of,
    relation_target_id,
    search_text_for,
    set_links,
    workspace_members,
)
from ymer.db import db_session
from ymer.formulas import FormulaError, compile_formula, references, rename_property
from ymer.models import (
    DatabaseProperty,
    DatabaseRow,
    DatabaseView,
    File,
    Page,
    PageType,
    PropertyType,
    Role,
    ViewType,
)
from ymer.permissions import WorkspaceScope

T = PropertyType

databases_bp = Blueprint(
    "databases", __name__, url_prefix="/workspaces/<uuid:workspace_id>/databases/<uuid:database_id>"
)


def _invalid(field: str, message: str) -> InvalidBody:
    return InvalidBody([{"field": field, "message": message}])


def _database(scope: WorkspaceScope, database_id: uuid.UUID, minimum: Role) -> Page:
    page = scope.get_page(database_id, minimum)
    if page.type is not PageType.DATABASE:
        raise NotFound("This page is not a database.")
    return page


def property_json(prop: DatabaseProperty) -> dict:
    return {
        "id": str(prop.id),
        "name": prop.name,
        "type": prop.type.value,
        "config": prop.config,
        "position": prop.position,
    }


def view_json(view: DatabaseView) -> dict:
    return {
        "id": str(view.id),
        "name": view.name,
        "type": view.type.value,
        "config": view.config,
        "position": view.position,
    }


def row_json(lr: LoadedRow) -> dict:
    return {
        "id": str(lr.row.id),
        "position": lr.row.position,
        "values": lr.values,
        "created_at": lr.row.created_at.isoformat() if lr.row.created_at else None,
        "updated_at": lr.row.updated_at.isoformat() if lr.row.updated_at else None,
    }


def _views(database_id: uuid.UUID) -> list[DatabaseView]:
    return list(
        db_session.scalars(
            select(DatabaseView)
            .where(DatabaseView.database_id == database_id)
            .order_by(DatabaseView.position, DatabaseView.created_at)
        )
    )


@databases_bp.get("")
@login_required
def get_database(workspace_id: uuid.UUID, database_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.VIEWER)
    return jsonify(
        properties=[property_json(p) for p in properties_of(db_session, page.id)],
        views=[view_json(v) for v in _views(page.id)],
    )


# --- rows -----------------------------------------------------------------------------


@databases_bp.get("/rows")
@login_required
def list_rows(workspace_id: uuid.UUID, database_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.VIEWER)
    props = properties_of(db_session, page.id)
    query = select(DatabaseRow).where(DatabaseRow.database_id == page.id)
    if q := (request.args.get("q") or "").strip():
        query = query.where(DatabaseRow.search_text.ilike(f"%{_escape_like(q)}%", escape="\\"))
    query = query.order_by(DatabaseRow.position, DatabaseRow.created_at)
    rows = RowReader(scope, page, props).load(list(db_session.scalars(query)))
    if view_id := request.args.get("view_id"):
        view = _get_view(page, view_id)
        rows = apply_view(props, rows, view.config)
    return jsonify(rows=[row_json(r) for r in rows])


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class RowBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    position: float | None = None


def _write_values(
    scope: WorkspaceScope,
    page: Page,
    props: list[DatabaseProperty],
    row: DatabaseRow,
    values: dict[str, Any],
) -> None:
    by_id = {str(p.id): p for p in props}
    members: dict[str, str] | None = None
    files: dict[str, str] | None = None
    stored = dict(row.values)
    for key, raw in values.items():
        prop = by_id.get(key)
        if prop is None:
            raise _invalid(f"values.{key}", "No such property.")
        try:
            if prop.type is T.RELATION:
                target = relation_target_id(prop)
                if raw and target is not None:
                    # Linking needs at least view access to the other database.
                    scope.get_page(target, Role.VIEWER)
                set_links(db_session, prop, row, raw)
                continue
            if prop.type not in STORED_TYPES:
                raise InvalidValue("Rollups and formulas are computed and cannot be set.")
            if prop.type is T.PERSON and members is None:
                members = workspace_members(db_session, page.workspace_id)
            if prop.type is T.FILE and files is None:
                files = _page_files(page)
            value = clean_value(
                prop,
                raw,
                member_ids=set(members or {}),
                member_names=names_to_members(members or {}),
                page_files=files,
            )
        except InvalidValue as exc:
            raise _invalid(f"values.{key}", str(exc)) from exc
        if value is None:
            stored.pop(key, None)
        else:
            stored[key] = value
    row.values = stored
    row.search_text = search_text_for(props, stored)
    row.updated_by_id = current_user().id


def _page_files(page: Page) -> dict[str, str]:
    return {
        str(fid): name
        for fid, name in db_session.execute(
            select(File.id, File.filename).where(File.page_id == page.id)
        )
    }


def _get_row(page: Page, row_id: uuid.UUID) -> DatabaseRow:
    row = db_session.scalar(
        select(DatabaseRow).where(DatabaseRow.id == row_id, DatabaseRow.database_id == page.id)
    )
    if row is None:
        raise NotFound("Row not found.")
    return row


def _one_row(scope: WorkspaceScope, page: Page, props, row: DatabaseRow) -> dict:
    return row_json(RowReader(scope, page, props).load([row])[0])


@databases_bp.post("/rows")
@login_required
def create_row(workspace_id: uuid.UUID, database_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    body = parse_body(RowBody)
    props = properties_of(db_session, page.id)
    row = DatabaseRow(
        workspace_id=page.workspace_id,
        database_id=page.id,
        values={},
        position=body.position
        if body.position is not None
        else next_row_position(db_session, page.id),
        created_by_id=current_user().id,
    )
    db_session.add(row)
    db_session.flush()  # links need the row id
    _write_values(scope, page, props, row, body.values)
    mark_dirty(db_session, [page.id])
    db_session.commit()
    return jsonify(row=_one_row(scope, page, props, row)), 201


@databases_bp.patch("/rows/<uuid:row_id>")
@login_required
def update_row(workspace_id: uuid.UUID, database_id: uuid.UUID, row_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    row = _get_row(page, row_id)
    body = parse_body(RowBody)
    props = properties_of(db_session, page.id)
    _write_values(scope, page, props, row, body.values)
    if body.position is not None:
        row.position = body.position
    mark_dirty(db_session, [page.id])
    db_session.commit()
    db_session.refresh(row)
    return jsonify(row=_one_row(scope, page, props, row))


@databases_bp.delete("/rows/<uuid:row_id>")
@login_required
def delete_row(workspace_id: uuid.UUID, database_id: uuid.UUID, row_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    db_session.delete(_get_row(page, row_id))  # links go with it (FK cascade)
    mark_dirty(db_session, [page.id])
    db_session.commit()
    return "", 204


# --- properties -----------------------------------------------------------------------


class CreatePropertyBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: PropertyType
    config: dict[str, Any] = Field(default_factory=dict)
    # Relations only: also add a property on the other database that shows the links
    # from its side.
    two_way: bool = False
    reverse_name: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name cannot be blank.")
        return value.strip()


class UpdatePropertyBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: PropertyType | None = None
    config: dict[str, Any] | None = None
    position: int | None = None


def _get_property(page: Page, prop_id: uuid.UUID) -> DatabaseProperty:
    prop = db_session.scalar(
        select(DatabaseProperty).where(
            DatabaseProperty.id == prop_id, DatabaseProperty.database_id == page.id
        )
    )
    if prop is None:
        raise NotFound("Property not found.")
    return prop


def _clean_config(
    scope: WorkspaceScope, page: Page, prop_type: PropertyType, config: dict[str, Any]
) -> dict[str, Any]:
    """Check the config for a type; keeps only the keys that type uses."""
    try:
        if prop_type in {T.SELECT, T.MULTI_SELECT}:
            return {"options": clean_options(config.get("options", []))}
        if prop_type is T.NUMBER:
            fmt = config.get("format", "number")
            if fmt not in {"number", "percent", "currency"}:
                raise InvalidValue("format must be number, percent or currency.")
            return {"format": fmt}
        if prop_type is T.ROLLUP:
            return _clean_rollup_config(page, config)
        if prop_type is T.FORMULA:
            return _clean_formula_config(page, config)
    except InvalidValue as exc:
        raise _invalid("config", str(exc)) from exc
    return {}


def _property_in(database_id: uuid.UUID | None, raw: Any, what: str) -> DatabaseProperty:
    try:
        prop_id = uuid.UUID(str(raw))
    except ValueError as exc:
        raise InvalidValue(f"{what} is missing.") from exc
    prop = db_session.scalar(
        select(DatabaseProperty).where(
            DatabaseProperty.id == prop_id, DatabaseProperty.database_id == database_id
        )
    )
    if prop is None:
        raise InvalidValue(f"{what} must be a property of the related database.")
    return prop


def _clean_rollup_filters(
    raw: Any, there: uuid.UUID | None, here: uuid.UUID, what: str
) -> list[dict]:
    """Filters on the rows a rollup reaches; a value can be ``{"this": <property id>}``,
    a property of the row being computed."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > 10:
        raise InvalidValue(f"{what} must be a list of at most 10 filters.")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise InvalidValue(f"Each of {what} is {{property_id, op, value}}.")
        prop = _property_in(there, item.get("property_id"), f"A filter's property in {what}")
        op = item.get("op")
        if op not in FILTER_OPS:
            raise InvalidValue(f"op must be one of {', '.join(sorted(FILTER_OPS))}.")
        value = item.get("value")
        if isinstance(value, dict):
            mine = db_session.scalar(
                select(DatabaseProperty).where(
                    DatabaseProperty.id == _uuid_or_none(value.get("this")),
                    DatabaseProperty.database_id == here,
                )
            )
            if mine is None:
                raise InvalidValue(
                    'A {"this": …} filter value must be a property of this database.'
                )
            value = {"this": str(mine.id)}
        out.append({"property_id": str(prop.id), "op": op, "value": value})
    return out


def _uuid_or_none(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _clean_rollup_config(page: Page, config: dict[str, Any]) -> dict[str, Any]:
    function = config.get("function", "count")
    if function not in ROLLUP_FUNCTIONS:
        raise InvalidValue(f"function must be one of {', '.join(sorted(ROLLUP_FUNCTIONS))}.")
    relation = db_session.scalar(
        select(DatabaseProperty).where(
            DatabaseProperty.id == _uuid_or_none(config.get("relation_property_id")),
            DatabaseProperty.database_id == page.id,
            DatabaseProperty.type == T.RELATION,
        )
    )
    if relation is None:
        raise InvalidValue("relation_property_id must be a relation in this database.")
    first_db = relation_target_id(relation)
    cleaned: dict[str, Any] = {"relation_property_id": str(relation.id)}
    last_db = first_db
    if filters := _clean_rollup_filters(config.get("filters"), first_db, page.id, "filters"):
        cleaned["filters"] = filters
    if config.get("through_property_id"):
        through = _property_in(first_db, config["through_property_id"], "through_property_id")
        if through.type is not T.RELATION:
            raise InvalidValue("through_property_id must be a relation in the related database.")
        last_db = relation_target_id(through)
        cleaned["through_property_id"] = str(through.id)
        if through_filters := _clean_rollup_filters(
            config.get("through_filters"), last_db, page.id, "through_filters"
        ):
            cleaned["through_filters"] = through_filters
    target = _property_in(last_db, config.get("target_property_id"), "target_property_id")
    cleaned["target_property_id"] = str(target.id)
    cleaned["function"] = function
    if function == "weighted_average":
        weight = _property_in(last_db, config.get("weight_property_id"), "weight_property_id")
        cleaned["weight_property_id"] = str(weight.id)
    if function == "top_option" and target.type not in {T.SELECT, T.MULTI_SELECT}:
        raise InvalidValue("Highest option works on select and multi-select properties.")
    return cleaned


def _clean_formula_config(page: Page, config: dict[str, Any]) -> dict[str, Any]:
    expression = config.get("expression")
    if not isinstance(expression, str):
        raise InvalidValue("A formula needs an expression.")
    try:
        tree = compile_formula(expression)
    except FormulaError as exc:
        raise InvalidValue(str(exc)) from exc
    names = {p.name.lower() for p in properties_of(db_session, page.id)}
    if missing := sorted(n for n in references(tree) if n.lower() not in names):
        raise InvalidValue(f"There is no property called {missing[0]!r}.")
    return {"expression": expression.strip()}


def _next_property_position(database_id: uuid.UUID) -> int:
    top = db_session.scalar(
        select(func.max(DatabaseProperty.position)).where(
            DatabaseProperty.database_id == database_id
        )
    )
    return (top if top is not None else -1) + 1


@databases_bp.post("/properties")
@login_required
def create_property(workspace_id: uuid.UUID, database_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    body = parse_body(CreatePropertyBody)
    if body.type is T.TITLE:
        raise _invalid("type", "A database has exactly one title property.")

    if body.type is T.RELATION:
        prop, reverse = _create_relation(scope, page, body)
        mark_dirty(db_session, [page.id])
        db_session.commit()
        result = {"property": property_json(prop)}
        if reverse is not None:
            result["reverse_property"] = property_json(reverse)
        return jsonify(result), 201

    prop = DatabaseProperty(
        workspace_id=page.workspace_id,
        database_id=page.id,
        name=body.name,
        type=body.type,
        config=_clean_config(scope, page, body.type, body.config),
        position=_next_property_position(page.id),
    )
    db_session.add(prop)
    mark_dirty(db_session, [page.id])
    db_session.commit()
    return jsonify(property=property_json(prop)), 201


def _create_relation(
    scope: WorkspaceScope, page: Page, body: CreatePropertyBody
) -> tuple[DatabaseProperty, DatabaseProperty | None]:
    try:
        target_id = uuid.UUID(str(body.config.get("database_id")))
    except ValueError as exc:
        raise _invalid("config.database_id", "Pick the database to relate to.") from exc
    try:
        # Seeing the other database is enough for a one-way relation; adding a property
        # to it (two-way) needs edit access there too.
        target = _database(scope, target_id, Role.EDITOR if body.two_way else Role.VIEWER)
    except (NotFound, Forbidden) as exc:
        raise _invalid("config.database_id", exc.description) from exc
    prop = DatabaseProperty(
        id=uuid.uuid4(),
        workspace_id=page.workspace_id,
        database_id=page.id,
        name=body.name,
        type=T.RELATION,
        config={"database_id": str(target.id)},
        position=_next_property_position(page.id),
    )
    db_session.add(prop)
    reverse = None
    if body.two_way:
        if target.id == page.id:
            raise _invalid("two_way", "A relation to the same database cannot be two-way.")
        reverse = DatabaseProperty(
            id=uuid.uuid4(),
            workspace_id=target.workspace_id,
            database_id=target.id,
            name=(body.reverse_name or "").strip() or (page.title or "Related"),
            type=T.RELATION,
            config={"database_id": str(page.id), "reverse_of": str(prop.id)},
            position=_next_property_position(target.id),
        )
        prop.config = {**prop.config, "reverse_property_id": str(reverse.id)}
        db_session.add(reverse)
    return prop, reverse


@databases_bp.patch("/properties/<uuid:prop_id>")
@login_required
def update_property(workspace_id: uuid.UUID, database_id: uuid.UUID, prop_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    prop = _get_property(page, prop_id)
    body = parse_body(UpdatePropertyBody)
    if body.name is not None:
        if not body.name.strip():
            raise _invalid("name", "Name cannot be blank.")
        if body.name.strip() != prop.name:
            _rename_in_formulas(page, prop.name, body.name.strip())
        prop.name = body.name.strip()
    if body.position is not None:
        prop.position = body.position

    new_type = body.type or prop.type
    if new_type is not prop.type:
        if prop.type not in CONVERTIBLE_TYPES or new_type not in CONVERTIBLE_TYPES:
            raise _invalid(
                "type",
                "Only plain properties can change type; title, relation, rollup and "
                "formula cannot.",
            )
        config = body.config if body.config is not None else {}
        _change_type(scope, page, prop, new_type, config)
    elif body.config is not None:
        if prop.type is T.RELATION:
            raise _invalid("config", "A relation's database cannot be changed.")
        prop.config = _clean_config(scope, page, prop.type, body.config)
    mark_dirty(db_session, [page.id])
    db_session.commit()
    return jsonify(property=property_json(prop))


def _rename_in_formulas(page: Page, old: str, new: str) -> None:
    """Formulas refer to properties by name: keep them pointing at the renamed one."""
    for formula in db_session.scalars(
        select(DatabaseProperty).where(
            DatabaseProperty.database_id == page.id, DatabaseProperty.type == T.FORMULA
        )
    ):
        expression = str(formula.config.get("expression") or "")
        updated = rename_property(expression, old, new)
        if updated != expression:
            formula.config = {**formula.config, "expression": updated}


def _change_type(
    scope: WorkspaceScope,
    page: Page,
    prop: DatabaseProperty,
    new_type: PropertyType,
    config: dict[str, Any],
) -> None:
    """Switch type and convert every row's value; text becomes options for selects."""
    key = str(prop.id)
    rows = list(db_session.scalars(select(DatabaseRow).where(DatabaseRow.database_id == page.id)))
    if new_type in {T.SELECT, T.MULTI_SELECT}:
        # Build options from the existing values (comma-separated for multi-select).
        texts: dict[uuid.UUID, list[str]] = {}
        for row in rows:
            value = convert_value(row.values.get(key), prop, T.TEXT)
            if value is None:
                continue
            parts = value.split(",") if new_type is T.MULTI_SELECT else [value]
            texts[row.id] = [p.strip()[:100] for p in parts if p.strip()]
        names = list(dict.fromkeys(n for parts in texts.values() for n in parts))
        try:
            options = clean_options(_merge_options(config.get("options", []), names))
        except InvalidValue as exc:
            raise _invalid("config", str(exc)) from exc
        by_name = {o["name"].lower(): o["id"] for o in options}
        for row in rows:
            values = dict(row.values)
            ids = [by_name[n.lower()] for n in texts.get(row.id, []) if n.lower() in by_name]
            ids = list(dict.fromkeys(ids))
            if not ids:
                values.pop(key, None)
            else:
                values[key] = ids[0] if new_type is T.SELECT else ids
            row.values = values
        prop.config = {"options": options}
    else:
        members = workspace_members(db_session, page.workspace_id)
        by_name = names_to_members(members)
        for row in rows:
            values = dict(row.values)
            value = convert_value(
                values.get(key), prop, new_type, people=members, member_names=by_name
            )
            if value is None or value is False:
                values.pop(key, None)
            else:
                values[key] = value
            row.values = values
        prop.config = _clean_config(scope, page, new_type, config)
    prop.type = new_type
    props = properties_of(db_session, page.id)
    for row in rows:
        row.search_text = search_text_for(props, row.values)


def _merge_options(given: Any, names: list[str]) -> list[dict]:
    options = [o for o in given if isinstance(o, dict)] if isinstance(given, list) else []
    known = {str(o.get("name", "")).strip().lower() for o in options}
    for name in names:
        if name and name.lower() not in known:
            options.append({"name": name})
            known.add(name.lower())
    return options


@databases_bp.delete("/properties/<uuid:prop_id>")
@login_required
def delete_property(workspace_id: uuid.UUID, database_id: uuid.UUID, prop_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    prop = _get_property(page, prop_id)
    if prop.type is T.TITLE:
        raise BadRequest("The title property cannot be deleted.")
    key = str(prop.id)
    if prop.type is T.RELATION:
        _detach_relation(prop)
    elif prop.type in STORED_TYPES:
        for row in db_session.scalars(
            select(DatabaseRow).where(
                DatabaseRow.database_id == page.id, DatabaseRow.values.has_key(key)
            )
        ):
            row.values = {k: v for k, v in row.values.items() if k != key}
    db_session.delete(prop)
    mark_dirty(db_session, [page.id])
    db_session.commit()
    return "", 204


def _detach_relation(prop: DatabaseProperty) -> None:
    """Deleting either half of a two-way relation removes the whole relation.

    The links are stored under the owning half, so they go with it (FK cascade).
    """
    other_id = prop.config.get("reverse_property_id") or prop.config.get("reverse_of")
    if not other_id:
        return
    other = db_session.get(DatabaseProperty, uuid.UUID(other_id))
    if other is not None:
        db_session.delete(other)


# --- views ----------------------------------------------------------------------------


class Sort(BaseModel):
    property_id: uuid.UUID
    direction: Literal["asc", "desc"] = "asc"


class Filter(BaseModel):
    property_id: uuid.UUID
    op: str
    value: Any = None

    @field_validator("op")
    @classmethod
    def _known_op(cls, value: str) -> str:
        if value not in FILTER_OPS:
            raise ValueError(f"op must be one of {', '.join(sorted(FILTER_OPS))}.")
        return value


class ViewConfig(BaseModel):
    sorts: list[Sort] = Field(default_factory=list, max_length=10)
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    group_by: uuid.UUID | None = None
    hidden: list[uuid.UUID] = Field(default_factory=list)
    widths: dict[str, int] = Field(default_factory=dict)
    # Chart views only: what the chart groups and measures (``ymer.charts``).
    chart: ChartConfig | None = None


class CreateViewBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: ViewType
    config: ViewConfig = Field(default_factory=ViewConfig)


class UpdateViewBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: ViewType | None = None
    config: ViewConfig | None = None
    position: int | None = None


def _get_view(page: Page, view_id: uuid.UUID | str) -> DatabaseView:
    try:
        view_uuid = uuid.UUID(str(view_id))
    except ValueError as exc:
        raise NotFound("View not found.") from exc
    view = db_session.scalar(
        select(DatabaseView).where(
            DatabaseView.id == view_uuid, DatabaseView.database_id == page.id
        )
    )
    if view is None:
        raise NotFound("View not found.")
    return view


def _config_json(config: ViewConfig) -> dict:
    return config.model_dump(mode="json")


@databases_bp.post("/views")
@login_required
def create_view(workspace_id: uuid.UUID, database_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    body = parse_body(CreateViewBody)
    top = db_session.scalar(
        select(func.max(DatabaseView.position)).where(DatabaseView.database_id == page.id)
    )
    view = DatabaseView(
        workspace_id=page.workspace_id,
        database_id=page.id,
        name=body.name.strip() or body.type.value.title(),
        type=body.type,
        config=_config_json(body.config),
        position=(top if top is not None else -1) + 1,
    )
    db_session.add(view)
    db_session.commit()
    return jsonify(view=view_json(view)), 201


@databases_bp.patch("/views/<uuid:view_id>")
@login_required
def update_view(workspace_id: uuid.UUID, database_id: uuid.UUID, view_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    view = _get_view(page, view_id)
    body = parse_body(UpdateViewBody)
    if body.name is not None and body.name.strip():
        view.name = body.name.strip()
    if body.type is not None:
        view.type = body.type
    if body.config is not None:
        view.config = _config_json(body.config)
    if body.position is not None:
        view.position = body.position
    db_session.commit()
    return jsonify(view=view_json(view))


@databases_bp.delete("/views/<uuid:view_id>")
@login_required
def delete_view(workspace_id: uuid.UUID, database_id: uuid.UUID, view_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.EDITOR)
    view = _get_view(page, view_id)
    if len(_views(page.id)) <= 1:
        raise BadRequest("A database needs at least one view.")
    db_session.delete(view)
    db_session.commit()
    return "", 204


# --- charts ---------------------------------------------------------------------------


class ChartBody(BaseModel):
    config: ChartConfig = Field(default_factory=ChartConfig)
    # Apply this view's filters first (a chart block can point at any view).
    view_id: uuid.UUID | None = None


@databases_bp.post("/chart")
@login_required
def chart(workspace_id: uuid.UUID, database_id: uuid.UUID):
    """Grouped and aggregated rows for a chart. A read: viewers may call it."""
    scope = scope_for(workspace_id)
    page = _database(scope, database_id, Role.VIEWER)
    body = parse_body(ChartBody)
    props = properties_of(db_session, page.id)
    rows = RowReader(scope, page, props).load()
    if body.view_id is not None:
        rows = apply_view(props, rows, _get_view(page, body.view_id).config)
    try:
        data = chart_data(props, rows, body.config)
    except ChartError as exc:
        raise _invalid("config", str(exc)) from exc
    return jsonify(chart=data)
