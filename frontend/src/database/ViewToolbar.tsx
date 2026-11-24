/** View settings: filters, sorts, visible properties and (for boards) the grouping. */
import { useQuery } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";

import { inputClass, Popover } from "@/components/Popover";
import { fetchDatabase, fetchRows, useMembers } from "@/lib/queries";
import type { DatabaseView, FilterOp, Property, ViewConfig, ViewFilter } from "@/lib/types";

import { useDatabaseContext } from "./context";
import { needsValue, opsFor, optionsOf, rowTitle, TYPE_ICON } from "./logic";

function ToolbarButton({ label, count, children }: { label: string; count?: number; children: (close: () => void) => ReactNode }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        ref={ref}
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={`rounded-md px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 ${count ? "text-brand-600" : "text-zinc-600 dark:text-zinc-300"}`}
      >
        {label}
        {count ? ` · ${count}` : ""}
      </button>
      {open && (
        <Popover anchor={ref.current} onClose={() => setOpen(false)} width={360} label={label}>
          {children(() => setOpen(false))}
        </Popover>
      )}
    </>
  );
}

function FilterValue({ property, filter, onChange }: { property: Property; filter: ViewFilter; onChange: (value: unknown) => void }) {
  const value = filter.value ?? "";
  if (!needsValue(filter.op)) return null;
  switch (property.type) {
    case "checkbox":
      return (
        <select className={inputClass} value={String(value === true || value === "true")} onChange={(e) => onChange(e.target.value === "true")}>
          <option value="true">Checked</option>
          <option value="false">Unchecked</option>
        </select>
      );
    case "select":
    case "multi_select":
      return (
        <select className={inputClass} value={String(value)} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">Choose…</option>
          {optionsOf(property).map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      );
    case "person":
      return <PersonChoice value={String(value)} onChange={onChange} />;
    case "relation":
      return <RelatedRowChoice property={property} value={String(value)} onChange={onChange} />;
    case "date":
      return <input type="date" className={inputClass} value={String(value).slice(0, 10)} onChange={(e) => onChange(e.target.value || null)} />;
    case "number":
    case "rollup":
      return <input type="number" step="any" className={inputClass} value={String(value)} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
    default:
      return <input className={inputClass} value={String(value)} placeholder="Value" onChange={(e) => onChange(e.target.value)} />;
  }
}

function PersonChoice({ value, onChange }: { value: string; onChange: (value: unknown) => void }) {
  const { workspaceId } = useDatabaseContext();
  const members = useMembers(workspaceId);
  return (
    <select aria-label="Person" className={inputClass} value={value} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">Choose…</option>
      {(members.data ?? []).map((m) => (
        <option key={m.user_id} value={m.user_id}>
          {m.display_name}
        </option>
      ))}
    </select>
  );
}

function RelatedRowChoice({ property, value, onChange }: { property: Property; value: string; onChange: (value: unknown) => void }) {
  const { workspaceId } = useDatabaseContext();
  const target = property.config.database_id ?? "";
  const schema = useQuery({ queryKey: ["database", workspaceId, target], queryFn: () => fetchDatabase(workspaceId, target), enabled: !!target });
  const rows = useQuery({ queryKey: ["rows", workspaceId, target, "picker", ""], queryFn: () => fetchRows(workspaceId, target, ""), enabled: !!target });
  return (
    <select aria-label="Related row" className={inputClass} value={value} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">Choose…</option>
      {(rows.data ?? []).map((r) => (
        <option key={r.id} value={r.id}>
          {rowTitle(r, schema.data?.properties ?? []) || "Untitled"}
        </option>
      ))}
    </select>
  );
}

export function ViewToolbar({ view, search, onSearch }: { view: DatabaseView; search: string; onSearch: (q: string) => void }) {
  const { properties, canEdit, mutations } = useDatabaseContext();
  const config = view.config;
  const save = (next: Partial<ViewConfig>) => mutations.updateView.mutate({ viewId: view.id, config: { ...config, ...next } });
  const byId = new Map(properties.map((p) => [p.id, p]));
  const filters = config.filters.filter((f) => byId.has(f.property_id));
  const sorts = config.sorts.filter((s) => byId.has(s.property_id));
  const groupable = properties.filter((p) => p.type === "select" || p.type === "multi_select");

  return (
    <div className="flex flex-wrap items-center gap-1">
      {view.type === "board" && canEdit && (
        <label className="flex items-center gap-1 text-sm text-zinc-600 dark:text-zinc-300">
          Group by
          <select
            value={config.group_by ?? ""}
            onChange={(e) => save({ group_by: e.target.value || null })}
            className="rounded-md bg-transparent px-1 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <option value="">{groupable.length ? "Choose…" : "Add a select property first"}</option>
            {groupable.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <ToolbarButton label="Filter" count={filters.length}>
        {() => (
          <div className="space-y-2 p-2">
            {!filters.length && <p className="text-xs text-zinc-500">No filters. Rows must match every filter to show.</p>}
            {filters.map((filter, i) => {
              const property = byId.get(filter.property_id)!;
              const update = (patch: Partial<ViewFilter>) =>
                save({ filters: filters.map((f, j) => (j === i ? { ...f, ...patch } : f)) });
              return (
                <div key={i} className="space-y-1 rounded-md border border-zinc-200 p-2 dark:border-zinc-800">
                  <div className="flex gap-1">
                    <select
                      aria-label="Filter property"
                      className={inputClass}
                      disabled={!canEdit}
                      value={filter.property_id}
                      onChange={(e) => {
                        const next = byId.get(e.target.value)!;
                        update({ property_id: next.id, op: opsFor(next.type)[0]!.op, value: null });
                      }}
                    >
                      {properties.map((p) => (
                        <option key={p.id} value={p.id}>
                          {TYPE_ICON[p.type]} {p.name}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label="Condition"
                      className={inputClass}
                      disabled={!canEdit}
                      value={filter.op}
                      onChange={(e) => update({ op: e.target.value as FilterOp })}
                    >
                      {opsFor(property.type).map((o) => (
                        <option key={o.op} value={o.op}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                    {canEdit && (
                      <button type="button" aria-label="Remove filter" onClick={() => save({ filters: filters.filter((_, j) => j !== i) })} className="px-1 text-zinc-400 hover:text-rose-600">
                        ×
                      </button>
                    )}
                  </div>
                  {canEdit && <FilterValue property={property} filter={filter} onChange={(value) => update({ value })} />}
                </div>
              );
            })}
            {canEdit && (
              <button
                type="button"
                className="text-sm text-brand-600"
                onClick={() => {
                  const first = properties[0]!;
                  save({ filters: [...filters, { property_id: first.id, op: opsFor(first.type)[0]!.op, value: null }] });
                }}
              >
                + Add a filter
              </button>
            )}
          </div>
        )}
      </ToolbarButton>
      <ToolbarButton label="Sort" count={sorts.length}>
        {() => (
          <div className="space-y-2 p-2">
            {!sorts.length && <p className="text-xs text-zinc-500">Rows keep their manual order.</p>}
            {sorts.map((sort, i) => (
              <div key={i} className="flex gap-1">
                <select
                  aria-label="Sort property"
                  className={inputClass}
                  disabled={!canEdit}
                  value={sort.property_id}
                  onChange={(e) => save({ sorts: sorts.map((s, j) => (j === i ? { ...s, property_id: e.target.value } : s)) })}
                >
                  {properties.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Direction"
                  className={inputClass}
                  disabled={!canEdit}
                  value={sort.direction}
                  onChange={(e) =>
                    save({ sorts: sorts.map((s, j) => (j === i ? { ...s, direction: e.target.value as "asc" | "desc" } : s)) })
                  }
                >
                  <option value="asc">Ascending</option>
                  <option value="desc">Descending</option>
                </select>
                {canEdit && (
                  <button type="button" aria-label="Remove sort" onClick={() => save({ sorts: sorts.filter((_, j) => j !== i) })} className="px-1 text-zinc-400 hover:text-rose-600">
                    ×
                  </button>
                )}
              </div>
            ))}
            {canEdit && (
              <button
                type="button"
                className="text-sm text-brand-600"
                onClick={() => save({ sorts: [...sorts, { property_id: properties[0]!.id, direction: "asc" }] })}
              >
                + Add a sort
              </button>
            )}
          </div>
        )}
      </ToolbarButton>
      <ToolbarButton label="Properties" count={config.hidden.filter((h) => byId.has(h)).length}>
        {() => (
          <div className="p-1">
            {properties
              .filter((p) => p.type !== "title")
              .map((p) => (
                <label key={p.id} className="flex items-center gap-2 rounded-md px-2 py-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                  <input
                    type="checkbox"
                    className="accent-brand-600"
                    disabled={!canEdit}
                    checked={!config.hidden.includes(p.id)}
                    onChange={(e) =>
                      save({ hidden: e.target.checked ? config.hidden.filter((h) => h !== p.id) : [...config.hidden, p.id] })
                    }
                  />
                  <span className="truncate">
                    {TYPE_ICON[p.type]} {p.name}
                  </span>
                </label>
              ))}
            {properties.length <= 1 && <p className="px-2 py-1 text-xs text-zinc-500">Only the title so far.</p>}
          </div>
        )}
      </ToolbarButton>
      <input
        type="search"
        aria-label="Search rows"
        placeholder="Search…"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        className="ml-1 w-32 rounded-md border border-transparent bg-zinc-100 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none sm:w-44 dark:bg-zinc-800"
      />
    </div>
  );
}
