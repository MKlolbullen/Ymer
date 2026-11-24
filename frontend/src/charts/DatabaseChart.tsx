import { useState } from "react";

import { ApiError } from "@/lib/api";
import { useChartData } from "@/lib/queries";
import type { ChartConfig, ChartData, Property } from "@/lib/types";

import { ChartCanvas } from "./ChartCanvas";
import { describeChart } from "./config";

/** The numbers behind a chart, for screen readers and anyone who wants exact values. */
function DataTable({ data }: { data: ChartData }) {
  const multi = data.series.length > 1;
  return (
    <div className="mt-2 max-h-64 overflow-auto rounded-md border border-zinc-200 dark:border-zinc-800">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-zinc-50 text-xs text-zinc-500 dark:bg-zinc-900">
          <tr>
            <th className="px-3 py-1.5 font-medium">Group</th>
            {data.series.map((s, i) => (
              <th key={i} className="px-3 py-1.5 text-right font-medium">
                {multi ? s.name : s.name || "Value"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.labels.map((label, i) => (
            <tr key={i} className="border-t border-zinc-100 dark:border-zinc-800">
              <td className="px-3 py-1">{label}</td>
              {data.series.map((s, j) => (
                <td key={j} className="px-3 py-1 text-right tabular-nums">
                  {s.data[i] ?? "–"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Props = {
  workspaceId: string;
  databaseId: string;
  config: ChartConfig;
  properties: Property[];
  viewId?: string | null;
  height?: number;
};

/** A chart of a database's rows: fetches the grouped data and draws it. */
export function DatabaseChart({ workspaceId, databaseId, config, properties, viewId = null, height }: Props) {
  const chart = useChartData(workspaceId, databaseId, config, viewId);
  const [showData, setShowData] = useState(false);
  const caption = describeChart(config, properties);

  if (chart.isError) {
    const status = chart.error instanceof ApiError ? chart.error.status : 0;
    return (
      <p className="py-6 text-center text-sm text-zinc-500">
        {status === 404
          ? "This chart's database was deleted, or you do not have access to it."
          : status === 422
            ? "The chart settings no longer fit this database. Pick the properties again."
            : "Could not load the chart."}
      </p>
    );
  }
  if (!chart.data) return <div style={{ height }} className="animate-pulse rounded-md bg-zinc-100 dark:bg-zinc-900" />;

  const data = chart.data;
  const empty = !data.labels.length || data.series.every((s) => s.data.every((v) => !v));
  return (
    <figure>
      <figcaption className="mb-2 flex items-baseline justify-between gap-3 text-xs text-zinc-500">
        <span>
          {caption} · {data.row_count} {data.row_count === 1 ? "row" : "rows"}
        </span>
        <button type="button" onClick={() => setShowData((v) => !v)} className="shrink-0 hover:text-zinc-900 dark:hover:text-white">
          {showData ? "Hide data" : "Show data"}
        </button>
      </figcaption>
      {empty ? (
        <p style={{ height }} className="flex items-center justify-center text-sm text-zinc-500">
          Nothing to chart yet. Add rows, or change what the chart groups by.
        </p>
      ) : (
        <ChartCanvas data={data} config={config} height={height} label={caption} />
      )}
      {showData && <DataTable data={data} />}
    </figure>
  );
}
