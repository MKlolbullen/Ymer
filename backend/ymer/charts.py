"""Charts over database rows: group rows by a property and aggregate a measure.

A chart is described by :class:`ChartConfig`. It is stored either in a database view of
type ``chart`` (``config.chart``) or in a chart block inside a document, and the
frontend draws what :func:`chart_data` returns with Chart.js.

Rows are loaded with :class:`ymer.databases.RowReader`, so a chart sees exactly what the
reader would see in a table: relation titles and rollups follow their permissions, and
a view's filters apply. Grouping runs in Python like the view filters do.

Grouping by property type:

- select: one group per option, in the property's option order, with its colour
- multi_select, person, relation: a row counts once in each of its values' groups
- checkbox: "Checked" and "Unchecked"
- date: bucketed by day, ISO week, month, quarter or year, oldest first
- anything else: the displayed value as text (numbers sort numerically)

Rows with no value go into a "No value" group (key ``None``), shown last unless the
config hides it.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from ymer.databases import LoadedRow, _tidy
from ymer.models import DatabaseProperty, PropertyType

T = PropertyType

ChartKind = Literal["bar", "horizontal_bar", "line", "area", "pie", "doughnut"]
Aggregate = Literal["count", "sum", "average", "min", "max"]
DateBucket = Literal["day", "week", "month", "quarter", "year"]

NO_VALUE = "No value"
MAX_GROUPS = 100
# Types whose values can be summed or averaged.
NUMERIC_TYPES = {T.NUMBER, T.ROLLUP, T.FORMULA}


class ChartConfig(BaseModel):
    kind: ChartKind = "bar"
    # Property the x axis (or pie slices) groups by. None draws one bar for all rows.
    x: uuid.UUID | None = None
    date_bucket: DateBucket = "month"
    # What each group measures: the row count, or an aggregate of a number property.
    aggregate: Aggregate = "count"
    measure: uuid.UUID | None = None
    # Optional second grouping: one series (line / stacked bar segment) per value.
    split_by: uuid.UUID | None = None
    stacked: bool = True
    hide_empty: bool = False
    sort: Literal["x", "value_desc", "value_asc"] = "x"
    limit: int = Field(default=30, ge=1, le=MAX_GROUPS)
    # Cumulative running total along the x axis (handy for "done over time").
    cumulative: bool = False


class ChartError(ValueError):
    """The config refers to properties that do not fit (wrong type, deleted)."""


@dataclass
class _Group:
    key: str | None
    label: str
    color: str | None = None
    order: tuple = ()


def _date_key(value: str, bucket: DateBucket) -> tuple[str, str]:
    """(sortable key, label) of the bucket an ISO date or datetime falls in."""
    day = date.fromisoformat(str(value)[:10])
    match bucket:
        case "day":
            return day.isoformat(), day.isoformat()
        case "week":
            year, week, _ = day.isocalendar()
            return f"{year}-W{week:02d}", f"{year} W{week:02d}"
        case "month":
            return f"{day.year}-{day.month:02d}", day.strftime("%b %Y")
        case "quarter":
            q = (day.month - 1) // 3 + 1
            return f"{day.year}-Q{q}", f"Q{q} {day.year}"
        case _:
            return str(day.year), str(day.year)


def _groups_of(prop: DatabaseProperty | None, value: Any, bucket: DateBucket) -> list[_Group]:
    """The groups one row's value puts it in. An empty value gives the "No value" group."""
    if prop is None:
        return [_Group(key="all", label="All rows")]
    t = prop.type
    empty = [_Group(key=None, label=NO_VALUE, order=(2,))]
    if t is T.CHECKBOX:
        checked = value is True
        return [
            _Group(
                key="checked" if checked else "unchecked",
                label="Checked" if checked else "Unchecked",
                order=(0, 0 if checked else 1),
            )
        ]
    if value is None or value == "" or value == []:
        return empty
    if t in {T.SELECT, T.MULTI_SELECT}:
        options = prop.config.get("options", [])
        index = {o["id"]: (i, o) for i, o in enumerate(options)}
        ids = value if isinstance(value, list) else [value]
        found = [
            _Group(
                key=i,
                label=index[i][1]["name"],
                color=index[i][1].get("color"),
                order=(0, index[i][0]),
            )
            for i in ids
            if i in index
        ]
        return found or empty
    if t is T.PERSON:
        return [
            _Group(
                key=p["id"] or p.get("display_name"),
                label=p.get("display_name") or "Someone",
                order=(0, p.get("display_name") or ""),
            )
            for p in value
        ] or empty
    if t is T.RELATION:
        return [
            _Group(
                key=r["id"],
                label=r.get("title") or "Untitled",
                order=(0, (r.get("title") or "").lower()),
            )
            for r in value
        ] or empty
    if t is T.FILE:
        return [
            _Group(key=f.get("name"), label=f.get("name") or "File", order=(0, f.get("name") or ""))
            for f in value
        ] or empty
    if t is T.DATE_RANGE:
        value = value.get("start") if isinstance(value, dict) else None
        if not value:
            return empty
    if t in {T.DATE, T.DATE_RANGE}:
        try:
            key, label = _date_key(value, bucket)
        except ValueError:
            return empty
        return [_Group(key=key, label=label, order=(0, key))]
    if isinstance(value, list):  # rollup "show original"
        return [_Group(key=str(v), label=str(v), order=(0, str(v).lower())) for v in value] or empty
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if isinstance(value, int | float):
        return [_Group(key=str(value), label=str(_tidy(value)), order=(0, 0, float(value)))]
    text = str(value)
    return [_Group(key=text, label=text, order=(0, 1, text.lower()))]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _aggregate(function: Aggregate, values: list[float], count: int) -> float | int | None:
    if function == "count":
        return count
    if not values:
        return None
    match function:
        case "sum":
            result = sum(values)
        case "average":
            result = sum(values) / len(values)
        case "min":
            result = min(values)
        case _:
            result = max(values)
    return _tidy(result)


def _check_props(config: ChartConfig, props: dict[str, DatabaseProperty]) -> None:
    for name in ("x", "split_by", "measure"):
        prop_id = getattr(config, name)
        if prop_id is not None and str(prop_id) not in props:
            raise ChartError(f"{name}: the property does not exist in this database.")
    if config.aggregate == "count":
        return
    if config.measure is None:
        raise ChartError("measure: choose a number property to aggregate.")
    if props[str(config.measure)].type not in NUMERIC_TYPES:
        raise ChartError("measure: only number and rollup properties can be aggregated.")


def chart_data(props: list[DatabaseProperty], rows: list[LoadedRow], config: ChartConfig) -> dict:
    """Labels and series for Chart.js.

    Returns ``{labels, colors, series: [{key, name, color, data}], row_count}``; ``colors``
    are option colour names for the x groups (or null), ``data[i]`` belongs to
    ``labels[i]`` and is null when a series has no rows in that group (except counts,
    which are 0).
    """
    by_id = {str(p.id): p for p in props}
    _check_props(config, by_id)
    x_prop = by_id.get(str(config.x)) if config.x else None
    split_prop = by_id.get(str(config.split_by)) if config.split_by else None
    measure_key = str(config.measure) if config.measure else None

    x_groups: dict[str | None, _Group] = {}
    s_groups: dict[str | None, _Group] = {}
    # (x key, series key) -> (numbers, row count)
    cells: dict[tuple, tuple[list[float], int]] = defaultdict(lambda: ([], 0))

    for lr in rows:
        xs = _groups_of(
            x_prop, lr.values.get(str(config.x)) if x_prop else None, config.date_bucket
        )
        ss = (
            _groups_of(split_prop, lr.values.get(str(config.split_by)), config.date_bucket)
            if split_prop
            else [_Group(key="value", label="")]
        )
        number = _number(lr.values.get(measure_key)) if measure_key else None
        for xg in xs:
            x_groups.setdefault(xg.key, xg)
            for sg in ss:
                s_groups.setdefault(sg.key, sg)
                numbers, count = cells[(xg.key, sg.key)]
                cells[(xg.key, sg.key)] = (
                    numbers + [number] if number is not None else numbers,
                    count + 1,
                )

    if config.hide_empty:
        x_groups.pop(None, None)
        s_groups.pop(None, None)

    series_list = sorted(s_groups.values(), key=lambda g: g.order)
    x_list = sorted(x_groups.values(), key=lambda g: g.order)

    def value(x_key, s_key):
        numbers, count = cells.get((x_key, s_key), ([], 0))
        if not count:
            return 0 if config.aggregate == "count" else None
        return _aggregate(config.aggregate, numbers, count)

    if config.sort != "x" and x_list:

        def total(g: _Group) -> float:
            return sum(value(g.key, s.key) or 0 for s in series_list)

        x_list.sort(key=total, reverse=config.sort == "value_desc")
    x_list = x_list[: config.limit]

    series = []
    for sg in series_list:
        data = [value(xg.key, sg.key) for xg in x_list]
        if config.cumulative:
            running, cumulative = 0.0, []
            for v in data:
                running += v or 0
                cumulative.append(_tidy(running))
            data = cumulative
        series.append(
            {
                "key": sg.key,
                "name": sg.label if split_prop else _measure_label(config, by_id),
                "color": sg.color,
                "data": data,
            }
        )
    return {
        "labels": [g.label for g in x_list],
        "colors": [g.color for g in x_list]
        if x_prop and x_prop.type in {T.SELECT, T.MULTI_SELECT}
        else None,
        "series": series,
        "row_count": len(rows),
    }


def _measure_label(config: ChartConfig, props: dict[str, DatabaseProperty]) -> str:
    if config.aggregate == "count":
        return "Count"
    name = props[str(config.measure)].name if config.measure else ""
    return f"{config.aggregate.title()} of {name}"
