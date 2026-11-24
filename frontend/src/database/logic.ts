/** Pure helpers for database views: labels, colors, grouping, formatting. */
import type {
  DatabaseView,
  FilterOp,
  Person,
  Property,
  PropertyType,
  RelationRef,
  RollupFunction,
  Row,
  SelectOption,
  ViewConfig,
} from "@/lib/types";

export const PROPERTY_TYPES: { type: PropertyType; label: string; icon: string }[] = [
  { type: "text", label: "Text", icon: "≡" },
  { type: "number", label: "Number", icon: "#" },
  { type: "select", label: "Select", icon: "◉" },
  { type: "multi_select", label: "Multi-select", icon: "☰" },
  { type: "date", label: "Date", icon: "📅" },
  { type: "checkbox", label: "Checkbox", icon: "☑" },
  { type: "url", label: "URL", icon: "🔗" },
  { type: "person", label: "Person", icon: "👤" },
  { type: "relation", label: "Relation", icon: "↗" },
  { type: "rollup", label: "Rollup", icon: "∑" },
];

export const TYPE_ICON: Record<PropertyType, string> = {
  title: "Aa",
  ...Object.fromEntries(PROPERTY_TYPES.map((t) => [t.type, t.icon])),
} as Record<PropertyType, string>;

/** Types that can be switched between without losing the property (server converts values). */
export const CONVERTIBLE: PropertyType[] = ["text", "number", "select", "multi_select", "date", "checkbox", "url", "person"];

export const ROLLUP_FUNCTIONS: { fn: RollupFunction; label: string }[] = [
  { fn: "show_original", label: "Show original" },
  { fn: "count", label: "Count all" },
  { fn: "count_values", label: "Count values" },
  { fn: "count_unique", label: "Count unique values" },
  { fn: "sum", label: "Sum" },
  { fn: "average", label: "Average" },
  { fn: "min", label: "Min / earliest" },
  { fn: "max", label: "Max / latest" },
  { fn: "percent_checked", label: "Percent checked" },
];

export const OPTION_COLORS = ["gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"] as const;

/** Tailwind classes per option color (light and dark). */
export const COLOR_CLASS: Record<string, string> = {
  gray: "bg-zinc-200 text-zinc-800 dark:bg-zinc-700 dark:text-zinc-100",
  brown: "bg-amber-900/15 text-amber-950 dark:bg-amber-900/50 dark:text-amber-100",
  orange: "bg-orange-200 text-orange-900 dark:bg-orange-900/60 dark:text-orange-100",
  yellow: "bg-yellow-200 text-yellow-900 dark:bg-yellow-800/60 dark:text-yellow-100",
  green: "bg-emerald-200 text-emerald-900 dark:bg-emerald-900/60 dark:text-emerald-100",
  blue: "bg-sky-200 text-sky-900 dark:bg-sky-900/60 dark:text-sky-100",
  purple: "bg-violet-200 text-violet-900 dark:bg-violet-900/60 dark:text-violet-100",
  pink: "bg-pink-200 text-pink-900 dark:bg-pink-900/60 dark:text-pink-100",
  red: "bg-rose-200 text-rose-900 dark:bg-rose-900/60 dark:text-rose-100",
};

const OP_LABEL: Record<FilterOp, string> = {
  equals: "is",
  not_equals: "is not",
  contains: "contains",
  not_contains: "does not contain",
  gt: "greater than",
  gte: "at least",
  lt: "less than",
  lte: "at most",
  is_empty: "is empty",
  is_not_empty: "is not empty",
};

/** The filter operators that make sense for a property type, with labels. */
export function opsFor(type: PropertyType): { op: FilterOp; label: string }[] {
  const ops: FilterOp[] = (() => {
    switch (type) {
      case "number":
      case "rollup":
        return ["equals", "not_equals", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"];
      case "date":
        return ["equals", "gt", "lt", "is_empty", "is_not_empty"];
      case "checkbox":
        return ["equals"];
      case "select":
        return ["equals", "not_equals", "is_empty", "is_not_empty"];
      case "multi_select":
      case "person":
      case "relation":
        return ["contains", "not_contains", "is_empty", "is_not_empty"];
      default:
        return ["contains", "not_contains", "equals", "not_equals", "is_empty", "is_not_empty"];
    }
  })();
  return ops.map((op) => ({
    op,
    label: type === "date" && op === "gt" ? "is after" : type === "date" && op === "lt" ? "is before" : OP_LABEL[op],
  }));
}

export function needsValue(op: FilterOp): boolean {
  return op !== "is_empty" && op !== "is_not_empty";
}

/** A view config with every key present (older views or the server may omit some). */
export function normalizeConfig(config: Partial<ViewConfig> | undefined): ViewConfig {
  return {
    sorts: config?.sorts ?? [],
    filters: config?.filters ?? [],
    group_by: config?.group_by ?? null,
    hidden: config?.hidden ?? [],
    widths: config?.widths ?? {},
    chart: config?.chart ?? null,
  };
}

export function titleProperty(properties: Property[]): Property | undefined {
  return properties.find((p) => p.type === "title");
}

export function rowTitle(row: Row, properties: Property[]): string {
  const title = titleProperty(properties);
  return (title && (row.values[title.id] as string | null)) || "";
}

export function visibleProperties(properties: Property[], view: DatabaseView): Property[] {
  const hidden = new Set(view.config.hidden ?? []);
  return properties.filter((p) => p.type === "title" || !hidden.has(p.id));
}

export function optionsOf(property: Property): SelectOption[] {
  return property.config.options ?? [];
}

export type BoardColumn = { option: SelectOption | null; rows: Row[] };

/** Board columns: one per option, in option order, plus "No value" first when it has cards. */
export function groupRows(rows: Row[], property: Property): BoardColumn[] {
  const options = optionsOf(property);
  const columns = new Map<string | null, BoardColumn>([[null, { option: null, rows: [] }]]);
  for (const option of options) columns.set(option.id, { option, rows: [] });
  for (const row of rows) {
    const raw = row.values[property.id];
    const ids = property.type === "multi_select" ? ((raw as string[] | null) ?? []) : raw ? [raw as string] : [];
    const known = ids.filter((id) => columns.has(id));
    if (!known.length) columns.get(null)!.rows.push(row);
    for (const id of known) columns.get(id)!.rows.push(row);
  }
  const empty = columns.get(null)!;
  columns.delete(null);
  return [...(empty.rows.length ? [empty] : []), ...columns.values()];
}

/** A position between two neighbours, for dropping a row between them. */
export function positionBetween(before: number | undefined, after: number | undefined): number {
  if (before === undefined && after === undefined) return 1;
  if (before === undefined) return after! - 1;
  if (after === undefined) return before + 1;
  return (before + after) / 2;
}

export function formatNumber(value: number, format: string | undefined): string {
  if (format === "percent") return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)}%`;
  if (format === "currency")
    return new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(value);
}

export function formatDate(value: string): string {
  const date = value.length === 10 ? new Date(`${value}T00:00:00`) : new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    ...(value.length > 10 ? { timeStyle: "short" } : {}),
  }).format(date);
}

/** A plain-text rendering of any value, for cards, rollups and tooltips. */
export function displayText(property: Property, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  switch (property.type) {
    case "number":
      return typeof value === "number" ? formatNumber(value, property.config.format) : String(value);
    case "checkbox":
      return value ? "✓" : "";
    case "date":
      return formatDate(String(value));
    case "select":
      return optionsOf(property).find((o) => o.id === value)?.name ?? "";
    case "multi_select": {
      const names = new Map(optionsOf(property).map((o) => [o.id, o.name]));
      return (value as string[]).map((id) => names.get(id) ?? "").filter(Boolean).join(", ");
    }
    case "person":
      return (value as Person[]).map((p) => p.display_name).join(", ");
    case "relation":
      return (value as RelationRef[]).map((r) => r.title ?? "Restricted").join(", ");
    case "rollup":
      if (Array.isArray(value)) return value.map((v) => (typeof v === "boolean" ? (v ? "✓" : "") : String(v))).join(", ");
      if (property.config.function === "percent_checked" && typeof value === "number") return `${value}%`;
      return typeof value === "number" ? formatNumber(value, undefined) : String(value);
    default:
      return String(value);
  }
}

/** The relation property ids (in the related database) that rollups can roll up. */
export function rollupTargets(properties: Property[]): Property[] {
  return properties.filter((p) => p.type !== "rollup");
}
