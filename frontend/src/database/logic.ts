/** Pure helpers for database views: labels, colors, grouping, formatting. */
import type {
  DatabaseView,
  DateRange,
  FileRef,
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
  { type: "formula", label: "Formula", icon: "ƒ" },
  { type: "date_range", label: "Date range", icon: "📆" },
  { type: "file", label: "Files", icon: "📎" },
];

/** Properties computed by the server: never edited in a cell. */
export const COMPUTED: PropertyType[] = ["rollup", "formula"];

export const TYPE_ICON: Record<PropertyType, string> = {
  title: "Aa",
  ...Object.fromEntries(PROPERTY_TYPES.map((t) => [t.type, t.icon])),
} as Record<PropertyType, string>;

/** Types that can be switched between without losing the property (server converts values). */
export const CONVERTIBLE: PropertyType[] = ["text", "number", "select", "multi_select", "date", "checkbox", "url", "person", "date_range", "file"];

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
  { fn: "median", label: "Median" },
  { fn: "weighted_average", label: "Weighted average" },
  { fn: "top_option", label: "Highest option (first in option order)" },
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
  in: "is any of",
  not_in: "is none of",
};

/** The filter operators that make sense for a property type, with labels. */
export function opsFor(type: PropertyType): { op: FilterOp; label: string }[] {
  const ops: FilterOp[] = (() => {
    switch (type) {
      case "number":
      case "rollup":
      case "formula":
        return ["equals", "not_equals", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"];
      case "date":
      case "date_range":
        return ["equals", "gt", "lt", "is_empty", "is_not_empty"];
      case "checkbox":
        return ["equals"];
      case "select":
        return ["equals", "not_equals", "is_empty", "is_not_empty"];
      case "multi_select":
      case "person":
      case "relation":
      case "file":
        return ["contains", "not_contains", "is_empty", "is_not_empty"];
      default:
        return ["contains", "not_contains", "equals", "not_equals", "is_empty", "is_not_empty"];
    }
  })();
  return ops.map((op) => ({
    op,
    label:
      type === "date_range" && op === "equals"
        ? "includes"
        : (type === "date" || type === "date_range") && op === "gt"
          ? "is after"
          : (type === "date" || type === "date_range") && op === "lt"
            ? "is before"
            : OP_LABEL[op],
  }));
}

/** Operators for a rollup filter: the view ones, plus "is any of" / "is none of" for selects. */
export function rollupOpsFor(type: PropertyType): { op: FilterOp; label: string }[] {
  const ops = opsFor(type);
  if (type === "select") ops.splice(2, 0, { op: "in", label: OP_LABEL.in }, { op: "not_in", label: OP_LABEL.not_in });
  return ops;
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

export function formatDateRange(value: DateRange): string {
  if (!value.end || value.end === value.start) return formatDate(value.start);
  return `${formatDate(value.start)} → ${formatDate(value.end)}`;
}

/** The file name to show for a file value. */
export function fileLabel(file: FileRef): string {
  return file.name || file.url?.split("/").pop() || "File";
}

/** A formula or rollup result as text: numbers formatted, lists joined, true as ✓. */
function computedText(value: unknown): string {
  if (Array.isArray(value)) return value.map(computedText).filter(Boolean).join(", ");
  if (typeof value === "boolean") return value ? "✓" : "";
  // Computed numbers (averages, percentages) show two decimals; the API keeps the full value.
  if (typeof value === "number") return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  if (value && typeof value === "object" && "start" in value) return formatDateRange(value as DateRange);
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}(T[\d:.]+)?$/.test(value)) return formatDate(value);
  return value === null || value === undefined ? "" : String(value);
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
      if (property.config.function === "percent_checked" && typeof value === "number") return `${value}%`;
      return computedText(value);
    case "formula":
      return computedText(value);
    case "date_range":
      return formatDateRange(value as DateRange);
    case "file":
      return (value as FileRef[]).map(fileLabel).join(", ");
    default:
      return String(value);
  }
}

/** The properties (in the related database) that rollups can roll up: any of them, including
 * other rollups and formulas; the server leaves a loop of properties empty. */
export function rollupTargets(properties: Property[]): Property[] {
  return properties;
}

/** The functions that fit a rolled-up property. */
export function rollupFunctionsFor(target: Property | undefined): { fn: RollupFunction; label: string }[] {
  if (!target) return ROLLUP_FUNCTIONS;
  return ROLLUP_FUNCTIONS.filter((f) => f.fn !== "top_option" || target.type === "select" || target.type === "multi_select");
}

/** Wrap a property name for a formula: prop("Name"), with quotes escaped. */
export function propRef(name: string): string {
  return `prop("${name.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}")`;
}
