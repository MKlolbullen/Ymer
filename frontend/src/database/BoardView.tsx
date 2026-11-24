import { useState } from "react";

import type { DatabaseView, Row } from "@/lib/types";

import { CellValue, OptionChip } from "./cells";
import { useDatabaseContext } from "./context";
import { groupRows, positionBetween, rowTitle, visibleProperties } from "./logic";

/** Kanban view: one column per option of the group-by select; drag cards across. */
export function BoardView({ view, rows }: { view: DatabaseView; rows: Row[] }) {
  const { properties, canEdit, mutations, openRow } = useDatabaseContext();
  const group = properties.find((p) => p.id === view.config.group_by && (p.type === "select" || p.type === "multi_select"));
  const [dragging, setDragging] = useState<{ row: Row; from: string | null } | null>(null);
  const [over, setOver] = useState<string | null>(null);

  if (!group) {
    return (
      <p className="rounded-lg border border-dashed border-zinc-300 p-6 text-sm text-zinc-500 dark:border-zinc-700">
        {canEdit ? "Pick a select property under “Group by” to lay the rows out as a board." : "This board has no grouping yet."}
      </p>
    );
  }

  const shown = visibleProperties(properties, view).filter((p) => p.type !== "title" && p.id !== group.id);
  const columns = groupRows(rows, group);

  /** Move the dragged card to `to` (an option id or null), placed before `beforeRow`. */
  const drop = (to: string | null, columnRows: Row[], beforeRow: Row | null) => {
    if (!dragging) return;
    const { row, from } = dragging;
    const others = columnRows.filter((r) => r.id !== row.id);
    const index = beforeRow ? others.findIndex((r) => r.id === beforeRow.id) : others.length;
    const position = positionBetween(others[index - 1]?.position, others[index]?.position);
    const body: { rowId: string; position: number; values?: Record<string, unknown> } = { rowId: row.id, position };
    if (from !== to) {
      if (group.type === "select") body.values = { [group.id]: to };
      else {
        const current = ((row.values[group.id] as string[] | null) ?? []).filter((id) => id !== from);
        body.values = { [group.id]: to ? [...new Set([...current, to])] : current };
      }
    }
    mutations.updateRow.mutate(body);
  };

  return (
    <div className="flex gap-3 overflow-x-auto pb-4">
      {columns.map(({ option, rows: columnRows }) => {
        const key = option?.id ?? "none";
        return (
          <section
            key={key}
            aria-label={option?.name ?? "No value"}
            onDragOver={(e) => {
              if (!dragging) return;
              e.preventDefault();
              setOver(key);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver((cur) => (cur === key ? null : cur));
            }}
            onDrop={(e) => {
              e.preventDefault();
              drop(option?.id ?? null, columnRows, null);
              setOver(null);
            }}
            className={`flex w-64 shrink-0 flex-col gap-2 rounded-lg p-2 ${over === key ? "bg-brand-50 dark:bg-zinc-800" : "bg-zinc-50 dark:bg-zinc-900"}`}
          >
            <header className="flex items-center gap-2 px-1 text-sm">
              {option ? <OptionChip option={option} /> : <span className="text-zinc-500">No {group.name}</span>}
              <span className="text-xs text-zinc-400">{columnRows.length}</span>
            </header>
            {columnRows.map((row) => (
              <article
                key={row.id}
                draggable={canEdit}
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", row.id);
                  setDragging({ row, from: option?.id ?? null });
                }}
                onDragEnd={() => {
                  setDragging(null);
                  setOver(null);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  drop(option?.id ?? null, columnRows, row);
                  setOver(null);
                }}
                onClick={() => openRow(row.id)}
                className={`cursor-pointer space-y-1 rounded-md border border-zinc-200 bg-white p-2 text-sm shadow-xs hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-950 ${
                  dragging?.row.id === row.id ? "opacity-50" : ""
                }`}
              >
                <p className="font-medium">{rowTitle(row, properties) || <span className="text-zinc-400">Untitled</span>}</p>
                {shown.map((p) =>
                  row.values[p.id] === null || row.values[p.id] === undefined ? null : (
                    <div key={p.id} className="text-xs text-zinc-600 dark:text-zinc-400" title={p.name}>
                      {p.type === "checkbox" ? `✓ ${p.name}` : <CellValue property={p} value={row.values[p.id]} />}
                    </div>
                  ),
                )}
              </article>
            ))}
            {canEdit && (
              <button
                type="button"
                onClick={() =>
                  mutations.createRow.mutate({
                    values: option ? { [group.id]: group.type === "select" ? option.id : [option.id] } : {},
                  })
                }
                className="rounded-md px-2 py-1 text-left text-sm text-zinc-500 hover:bg-zinc-200/60 dark:hover:bg-zinc-800"
              >
                + New
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}
