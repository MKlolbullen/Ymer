import type { ChartAggregate, ChartConfig, ChartKind, DateBucket, Property, PropertyType } from "@/lib/types";

export const CHART_KINDS: { kind: ChartKind; label: string }[] = [
  { kind: "bar", label: "Bar" },
  { kind: "horizontal_bar", label: "Horizontal bar" },
  { kind: "line", label: "Line" },
  { kind: "area", label: "Area" },
  { kind: "pie", label: "Pie" },
  { kind: "doughnut", label: "Doughnut" },
];

export const AGGREGATES: { value: ChartAggregate; label: string }[] = [
  { value: "count", label: "Count rows" },
  { value: "sum", label: "Sum" },
  { value: "average", label: "Average" },
  { value: "min", label: "Minimum" },
  { value: "max", label: "Maximum" },
];

export const DATE_BUCKETS: { value: DateBucket; label: string }[] = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "quarter", label: "Quarter" },
  { value: "year", label: "Year" },
];

/** Properties a chart can group by (everything but rollups of rollups, which the server refuses anyway). */
const GROUPABLE: PropertyType[] = ["title", "text", "number", "select", "multi_select", "date", "checkbox", "url", "person", "relation", "rollup"];
const NUMERIC: PropertyType[] = ["number", "rollup"];

export const groupable = (properties: Property[]) => properties.filter((p) => GROUPABLE.includes(p.type));
export const numeric = (properties: Property[]) => properties.filter((p) => NUMERIC.includes(p.type));

export const isPie = (kind: ChartKind) => kind === "pie" || kind === "doughnut";

const BEST_X: PropertyType[] = ["select", "multi_select", "person", "checkbox", "date"];

/** A sensible first chart: count rows by the first select-like property. */
export function defaultChartConfig(properties: Property[]): ChartConfig {
  const x = BEST_X.map((t) => properties.find((p) => p.type === t)).find(Boolean) ?? null;
  return {
    kind: "bar",
    x: x?.id ?? null,
    date_bucket: "month",
    aggregate: "count",
    measure: null,
    split_by: null,
    stacked: true,
    hide_empty: false,
    sort: "x",
    limit: 30,
    cumulative: false,
  };
}

/** Fill gaps in a stored config (older or partial) so every field is set. */
export function normalizeChartConfig(config: Partial<ChartConfig> | null | undefined, properties: Property[]): ChartConfig {
  const base = defaultChartConfig(properties);
  if (!config) return base;
  const merged = { ...base, ...config, x: config.x === undefined ? base.x : config.x };
  const ids = new Set(properties.map((p) => p.id));
  // Drop references to deleted properties rather than failing the whole chart.
  if (merged.x && !ids.has(merged.x)) merged.x = null;
  if (merged.split_by && !ids.has(merged.split_by)) merged.split_by = null;
  if (merged.measure && !ids.has(merged.measure)) merged.measure = null;
  if (merged.aggregate !== "count" && !merged.measure) merged.aggregate = "count";
  return merged;
}

/** One line describing a chart, e.g. "Sum of Amount by Stage". */
export function describeChart(config: ChartConfig, properties: Property[]): string {
  const name = (id: string | null) => properties.find((p) => p.id === id)?.name ?? "";
  const what =
    config.aggregate === "count"
      ? "Rows"
      : `${AGGREGATES.find((a) => a.value === config.aggregate)?.label} of ${name(config.measure)}`;
  const by = config.x ? ` by ${name(config.x)}` : "";
  const split = config.split_by ? `, split by ${name(config.split_by)}` : "";
  return `${what}${by}${split}`;
}
