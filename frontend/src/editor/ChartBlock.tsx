/**
 * A chart of a database inside a document.
 *
 * Stored as a `databaseChart` block whose attributes are the database id, an optional
 * view id (whose filters apply) and the chart config as a JSON string. The chart itself
 * is always drawn from live rows, so it updates as the database changes. Readers who
 * cannot see the database get a notice instead of the chart.
 */
import { mergeAttributes, Node } from "@tiptap/core";
import { NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from "@tiptap/react";
import { useMemo, useState } from "react";

import { ChartSettings } from "@/charts/ChartSettings";
import { normalizeChartConfig } from "@/charts/config";
import { DatabaseChart } from "@/charts/DatabaseChart";
import { inputClass } from "@/components/Popover";
import { useDatabase, usePages } from "@/lib/queries";
import type { ChartConfig } from "@/lib/types";

type ChartAttrs = { databaseId: string | null; viewId: string | null; config: string | null };
export type ChartBlockOptions = { workspaceId: string };

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    databaseChart: { insertChart: () => ReturnType };
  }
}

function parseConfig(raw: string | null): Partial<ChartConfig> | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Partial<ChartConfig>;
  } catch {
    return null;
  }
}

function ChartBlockView({ node, updateAttributes, deleteNode, editor, selected, extension }: ReactNodeViewProps) {
  const { workspaceId } = extension.options as ChartBlockOptions;
  const attrs = node.attrs as ChartAttrs;
  const editable = editor.isEditable;
  const [editing, setEditing] = useState(!attrs.databaseId);
  const pages = usePages(workspaceId);
  const databases = (pages.data ?? []).filter((p) => p.type === "database");
  const schema = useDatabase(workspaceId, attrs.databaseId ?? "");
  const properties = useMemo(
    () => [...(schema.data?.properties ?? [])].sort((a, b) => a.position - b.position),
    [schema.data],
  );
  const config = useMemo(() => normalizeChartConfig(parseConfig(attrs.config), properties), [attrs.config, properties]);
  const database = databases.find((d) => d.id === attrs.databaseId);

  return (
    <NodeViewWrapper
      contentEditable={false}
      data-drag-handle
      className={`my-4 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800 ${selected ? "ring-2 ring-brand-500" : ""}`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm font-medium">📊 {database ? database.title || "Untitled database" : "Chart"}</div>
        {editable && (
          <div className="flex shrink-0 gap-1 text-xs">
            <button type="button" onClick={() => setEditing((v) => !v)} className="rounded px-2 py-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
              {editing ? "Done" : "Edit chart"}
            </button>
            <button type="button" onClick={deleteNode} className="rounded px-2 py-1 text-zinc-500 hover:bg-zinc-100 hover:text-rose-600 dark:hover:bg-zinc-800">
              Remove
            </button>
          </div>
        )}
      </div>

      {editable && editing && (
        <div className="mb-3 rounded-md bg-zinc-50 p-1 dark:bg-zinc-900">
          <div className="grid gap-3 p-2 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-zinc-500">Database</span>
              <select
                className={inputClass}
                value={attrs.databaseId ?? ""}
                onChange={(e) => updateAttributes({ databaseId: e.target.value || null, viewId: null, config: null })}
              >
                <option value="">Choose a database…</option>
                {databases.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title || "Untitled"}
                  </option>
                ))}
              </select>
            </label>
            {attrs.databaseId && (
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-zinc-500">Rows from view</span>
                <select className={inputClass} value={attrs.viewId ?? ""} onChange={(e) => updateAttributes({ viewId: e.target.value || null })}>
                  <option value="">All rows</option>
                  {(schema.data?.views ?? []).map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name} (its filters)
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {attrs.databaseId && schema.data && (
            <ChartSettings config={config} properties={properties} onChange={(next) => updateAttributes({ config: JSON.stringify(next) })} />
          )}
          {!databases.length && pages.data && <p className="px-2 pb-2 text-xs text-zinc-500">Create a database page first; charts draw its rows.</p>}
        </div>
      )}

      {attrs.databaseId ? (
        schema.isError ? (
          <p className="py-6 text-center text-sm text-zinc-500">This chart's database was deleted, or you do not have access to it.</p>
        ) : schema.data ? (
          <DatabaseChart workspaceId={workspaceId} databaseId={attrs.databaseId} viewId={attrs.viewId} config={config} properties={properties} height={280} />
        ) : (
          <div className="h-[280px] animate-pulse rounded-md bg-zinc-100 dark:bg-zinc-900" />
        )
      ) : (
        !editable && <p className="py-6 text-center text-sm text-zinc-500">This chart has no database yet.</p>
      )}
    </NodeViewWrapper>
  );
}

export const DatabaseChartBlock = Node.create<ChartBlockOptions>({
  name: "databaseChart",
  group: "block",
  atom: true,
  draggable: true,
  addOptions() {
    return { workspaceId: "" };
  },
  addAttributes() {
    return {
      databaseId: { default: null, parseHTML: (el) => el.getAttribute("data-database-id") },
      viewId: { default: null, parseHTML: (el) => el.getAttribute("data-view-id") },
      config: { default: null, parseHTML: (el) => el.getAttribute("data-config") },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-database-chart]" }];
  },
  renderHTML({ HTMLAttributes, node }) {
    return [
      "div",
      mergeAttributes(HTMLAttributes, {
        "data-database-chart": "",
        "data-database-id": node.attrs.databaseId,
        "data-view-id": node.attrs.viewId,
        "data-config": node.attrs.config,
      }),
    ];
  },
  addNodeView() {
    return ReactNodeViewRenderer(ChartBlockView);
  },
  addCommands() {
    return {
      insertChart:
        () =>
        ({ commands }) =>
          commands.insertContent({ type: this.name }),
    };
  },
});
