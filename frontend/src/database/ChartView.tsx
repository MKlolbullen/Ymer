import { useState } from "react";

import { ChartSettings } from "@/charts/ChartSettings";
import { normalizeChartConfig } from "@/charts/config";
import { DatabaseChart } from "@/charts/DatabaseChart";
import type { ChartConfig, DatabaseView } from "@/lib/types";

import { useDatabaseContext } from "./context";

/** A database view drawn as a chart. Its filters (toolbar) apply before grouping. */
export function ChartView({ view }: { view: DatabaseView }) {
  const { workspaceId, databaseId, properties, canEdit, mutations } = useDatabaseContext();
  const config = normalizeChartConfig(view.config.chart, properties);
  const [editing, setEditing] = useState(!view.config.chart && canEdit);

  const save = (chart: ChartConfig) => mutations.updateView.mutate({ viewId: view.id, config: { ...view.config, chart } });

  return (
    <div className="space-y-3">
      {canEdit && (
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800">
          <button
            type="button"
            aria-expanded={editing}
            onClick={() => setEditing((v) => !v)}
            className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium text-zinc-600 dark:text-zinc-300"
          >
            Chart settings
            <span aria-hidden>{editing ? "▴" : "▾"}</span>
          </button>
          {editing && (
            <div className="border-t border-zinc-200 dark:border-zinc-800">
              <ChartSettings config={config} properties={properties} onChange={save} />
            </div>
          )}
        </div>
      )}
      <DatabaseChart workspaceId={workspaceId} databaseId={databaseId} viewId={view.id} config={config} properties={properties} height={380} />
    </div>
  );
}
