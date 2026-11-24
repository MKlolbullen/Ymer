import { useRef, useState } from "react";

import { Popover } from "@/components/Popover";
import type { DatabaseView, Property, Row } from "@/lib/types";

import { Cell } from "./cells";
import { useDatabaseContext } from "./context";
import { positionBetween, TYPE_ICON, visibleProperties } from "./logic";
import { AddPropertyForm, PropertyMenu } from "./PropertyMenu";

function HeaderCell({ property, view }: { property: Property; view: DatabaseView }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const sort = view.config.sorts.find((s) => s.property_id === property.id);
  return (
    <th scope="col" className="border-r border-b border-zinc-200 p-0 text-left font-normal dark:border-zinc-800" style={{ minWidth: property.type === "title" ? 240 : 160 }}>
      <button
        ref={ref}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-xs text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900"
      >
        <span aria-hidden className="w-4 text-center">
          {TYPE_ICON[property.type]}
        </span>
        <span className="truncate">{property.name}</span>
        {sort && <span aria-label={`sorted ${sort.direction}`}>{sort.direction === "asc" ? "↑" : "↓"}</span>}
      </button>
      {open && (
        <Popover anchor={ref.current} onClose={() => setOpen(false)} width={300} label={`${property.name} settings`}>
          <PropertyMenu property={property} view={view} onClose={() => setOpen(false)} />
        </Popover>
      )}
    </th>
  );
}

function AddColumn() {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <th scope="col" className="w-10 border-b border-zinc-200 p-0 dark:border-zinc-800">
      <button
        ref={ref}
        type="button"
        aria-label="Add a property"
        title="Add a property"
        onClick={() => setOpen(true)}
        className="w-full px-3 py-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-900"
      >
        +
      </button>
      {open && (
        <Popover anchor={ref.current} onClose={() => setOpen(false)} width={300} label="Add a property">
          <AddPropertyForm onDone={() => setOpen(false)} />
        </Popover>
      )}
    </th>
  );
}

/** Spreadsheet-style view. Without sorts, rows can be dragged into a manual order. */
export function TableView({ view, rows }: { view: DatabaseView; rows: Row[] }) {
  const { properties, canEdit, mutations, openRow } = useDatabaseContext();
  const columns = visibleProperties(properties, view);
  const manual = canEdit && view.config.sorts.length === 0;
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const drop = (targetId: string) => {
    if (!dragging || dragging === targetId) return;
    const without = rows.filter((r) => r.id !== dragging);
    const index = without.findIndex((r) => r.id === targetId);
    const position = positionBetween(without[index - 1]?.position, without[index]?.position);
    mutations.updateRow.mutate({ rowId: dragging, position });
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-zinc-50 dark:bg-zinc-900/50">
          <tr>
            {manual && <th className="w-6 border-b border-zinc-200 dark:border-zinc-800" aria-label="Reorder" />}
            {columns.map((p) => (
              <HeaderCell key={p.id} property={p} view={view} />
            ))}
            {canEdit && <AddColumn />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              onDragOver={(e) => {
                if (!dragging) return;
                e.preventDefault();
                setOver(row.id);
              }}
              onDragLeave={() => setOver((cur) => (cur === row.id ? null : cur))}
              onDrop={(e) => {
                e.preventDefault();
                drop(row.id);
                setOver(null);
              }}
              className={`group ${over === row.id && dragging !== row.id ? "border-t-2 border-t-brand-500" : ""} ${dragging === row.id ? "opacity-50" : ""}`}
            >
              {manual && (
                <td className="border-b border-zinc-200 p-0 text-center align-middle dark:border-zinc-800">
                  <span
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData("text/plain", row.id);
                      setDragging(row.id);
                    }}
                    onDragEnd={() => {
                      setDragging(null);
                      setOver(null);
                    }}
                    title="Drag to reorder"
                    className="invisible cursor-grab px-1 text-zinc-400 select-none group-hover:visible"
                  >
                    ⋮⋮
                  </span>
                </td>
              )}
              {columns.map((p) => (
                <td key={p.id} className="relative max-w-96 border-r border-b border-zinc-200 p-0 align-top dark:border-zinc-800">
                  <Cell row={row} property={p} />
                  {p.type === "title" && (
                    <button
                      type="button"
                      onClick={() => openRow(row.id)}
                      className="invisible absolute top-1.5 right-1 rounded border border-zinc-200 bg-white px-1.5 text-xs text-zinc-600 group-hover:visible dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                    >
                      Open
                    </button>
                  )}
                </td>
              ))}
              {canEdit && <td className="border-b border-zinc-200 dark:border-zinc-800" />}
            </tr>
          ))}
        </tbody>
      </table>
      {canEdit && (
        <button
          type="button"
          onClick={() => mutations.createRow.mutate({})}
          className="w-full px-3 py-2 text-left text-sm text-zinc-500 hover:bg-zinc-50 dark:hover:bg-zinc-900"
        >
          + New
        </button>
      )}
      {!rows.length && !canEdit && <p className="px-3 py-4 text-sm text-zinc-500">No rows.</p>}
    </div>
  );
}
