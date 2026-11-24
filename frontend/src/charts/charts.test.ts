import { expect, test } from "vitest";

import type { ChartData, Property } from "@/lib/types";

import { chartJsConfig } from "./ChartCanvas";
import { defaultChartConfig, describeChart, normalizeChartConfig } from "./config";

const props: Property[] = [
  { id: "t", name: "Name", type: "title", config: {}, position: 0 },
  { id: "amount", name: "Amount", type: "number", config: {}, position: 1 },
  { id: "stage", name: "Stage", type: "select", config: { options: [] }, position: 2 },
];

test("the default chart counts rows by the first select", () => {
  const config = defaultChartConfig(props);
  expect(config).toMatchObject({ kind: "bar", x: "stage", aggregate: "count", measure: null });
  expect(describeChart(config, props)).toBe("Rows by Stage");
});

test("normalize drops deleted properties and an aggregate with nothing to measure", () => {
  const config = normalizeChartConfig({ x: "gone", aggregate: "sum", measure: "gone", kind: "line" }, props);
  expect(config).toMatchObject({ kind: "line", x: null, measure: null, aggregate: "count" });
  expect(describeChart({ ...config, aggregate: "sum", measure: "amount", x: "stage" }, props)).toBe("Sum of Amount by Stage");
});

const data: ChartData = {
  labels: ["Lead", "Won"],
  colors: ["gray", "green"],
  series: [{ key: "value", name: "Count", color: null, data: [1, 2] }],
  row_count: 3,
};

test("a single series uses option colours per bar and hides the legend", () => {
  const cfg = chartJsConfig(data, defaultChartConfig(props), "light");
  expect(cfg.type).toBe("bar");
  const ds = cfg.data.datasets[0] as unknown as { backgroundColor: string[] };
  expect(ds.backgroundColor).toEqual(["#8a8984", "#1baf7a"]);
  expect((cfg.options as { plugins: { legend: { display: boolean } } }).plugins.legend.display).toBe(false);
});

test("pies sum several series per slice", () => {
  const split: ChartData = {
    ...data,
    series: [
      { key: "a", name: "A", color: null, data: [1, 2] },
      { key: "b", name: "B", color: null, data: [3, null] },
    ],
  };
  const cfg = chartJsConfig(split, { ...defaultChartConfig(props), kind: "pie" }, "dark");
  expect(cfg.type).toBe("pie");
  expect(cfg.data.datasets[0]!.data).toEqual([4, 2]);
});
