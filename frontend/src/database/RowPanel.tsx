import { X } from "lucide-react";
import { useEffect } from "react";

import { Icon, PropertyIcon } from "@/components/icons";
import { timeAgo } from "@/lib/format";
import type { Row } from "@/lib/types";

import { Cell } from "./cells";
import { useDatabaseContext } from "./context";

/** A row opened on its own: every property, editable, in a side panel. */
export function RowPanel({ row, onClose }: { row: Row; onClose: () => void }) {
  const { properties, canEdit, mutations } = useDatabaseContext();
  const title = properties.find((p) => p.type === "title");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector("[data-popover]")) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside
      aria-label="Row"
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-200 px-4 dark:border-zinc-800">
        <span className="text-xs text-zinc-500">Edited {timeAgo(row.updated_at)}</span>
        <div className="flex gap-1">
          {canEdit && (
            <button
              type="button"
              onClick={() => {
                if (!window.confirm("Delete this row?")) return;
                mutations.deleteRow.mutate(row.id);
                onClose();
              }}
              className="rounded-md px-2 py-1 text-sm text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950"
            >
              Delete
            </button>
          )}
          <button type="button" aria-label="Close" onClick={onClose} className="rounded-md p-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <Icon icon={X} />
          </button>
        </div>
      </div>
      <div className="flex-1 space-y-1 overflow-y-auto p-4">
        {title && (
          <div className="mb-3 text-xl">
            <Cell row={row} property={title} variant="panel" />
          </div>
        )}
        {properties
          .filter((p) => p.type !== "title")
          .map((p) => (
            <div key={p.id} className="grid grid-cols-[8rem_1fr] items-start gap-2 text-sm">
              <span className="truncate py-1.5 text-zinc-500" title={p.name}>
                <PropertyIcon type={p.type} size={14} className="mr-1 inline-block align-[-2px]" />
                {p.name}
              </span>
              <div className="min-w-0 rounded-md">
                <Cell row={row} property={p} variant="panel" />
              </div>
            </div>
          ))}
      </div>
    </aside>
  );
}
