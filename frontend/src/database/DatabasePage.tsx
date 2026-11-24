/** A database page: view tabs, the view's toolbar, and the table or board itself. */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { inputClass, MenuItem, Popover } from "@/components/Popover";
import { useDatabase, useDatabaseMutations, useRows } from "@/lib/queries";
import { atLeast, type DatabaseView, type Page, type Property, type Row, type SelectOption, type ViewType } from "@/lib/types";

const VIEW_ICON: Record<ViewType, string> = { table: "▤", board: "▥", chart: "📊" };
const VIEW_LABEL: Record<ViewType, string> = { table: "Table", board: "Board", chart: "Chart" };

import { BoardView } from "./BoardView";
import { ChartView } from "./ChartView";
import { DatabaseContext, useDatabaseContext, type DatabaseContextValue } from "./context";
import { normalizeConfig, OPTION_COLORS } from "./logic";
import { RowPanel } from "./RowPanel";
import { TableView } from "./TableView";
import { ViewToolbar } from "./ViewToolbar";

/** Values that can be shown before the server answers (refs need the server's lookup). */
const OPTIMISTIC = new Set(["title", "text", "number", "select", "multi_select", "date", "checkbox", "url"]);

function ViewTab({ view, active, onSelect, canDelete }: { view: DatabaseView; active: boolean; onSelect: () => void; canDelete: boolean }) {
  const { canEdit, mutations } = useDatabaseContext();
  const ref = useRef<HTMLButtonElement>(null);
  const [menu, setMenu] = useState(false);
  const [name, setName] = useState(view.name);
  return (
    <>
      <button
        ref={ref}
        type="button"
        role="tab"
        aria-selected={active}
        onClick={() => (active && canEdit ? setMenu(true) : onSelect())}
        className={`border-b-2 px-2 py-1.5 text-sm ${active ? "border-zinc-900 font-medium dark:border-white" : "border-transparent text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}
      >
        {VIEW_ICON[view.type]} {view.name}
      </button>
      {menu && (
        <Popover anchor={ref.current} onClose={() => setMenu(false)} label="View settings">
          <form
            className="p-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) mutations.updateView.mutate({ viewId: view.id, name: name.trim() });
              setMenu(false);
            }}
          >
            <input aria-label="View name" className={inputClass} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          </form>
          {(["table", "board", "chart"] as const)
            .filter((t) => t !== view.type)
            .map((t) => (
              <MenuItem
                key={t}
                onClick={() => {
                  mutations.updateView.mutate({ viewId: view.id, type: t });
                  setMenu(false);
                }}
              >
                Show as {t}
              </MenuItem>
            ))}
          {canDelete && (
            <MenuItem
              danger
              onClick={() => {
                if (window.confirm(`Delete the “${view.name}” view? Rows are kept.`)) mutations.deleteView.mutate(view.id);
                setMenu(false);
              }}
            >
              Delete view
            </MenuItem>
          )}
        </Popover>
      )}
    </>
  );
}

function AddView({ onCreated, properties }: { onCreated: (id: string) => void; properties: Property[] }) {
  const { mutations } = useDatabaseContext();
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const create = async (type: ViewType) => {
    setOpen(false);
    const group = properties.find((p) => p.type === "select" || p.type === "multi_select");
    const view = await mutations.createView.mutateAsync({
      name: VIEW_LABEL[type],
      type,
      config: type === "board" && group ? { group_by: group.id } : {},
    });
    onCreated(view.id);
  };
  return (
    <>
      <button ref={ref} type="button" aria-label="Add a view" title="Add a view" onClick={() => setOpen(true)} className="px-2 py-1.5 text-sm text-zinc-400 hover:text-zinc-900 dark:hover:text-white">
        +
      </button>
      {open && (
        <Popover anchor={ref.current} onClose={() => setOpen(false)} width={180} label="Add a view">
          <MenuItem onClick={() => void create("table")}>▤ Table</MenuItem>
          <MenuItem onClick={() => void create("board")}>▥ Board</MenuItem>
          <MenuItem onClick={() => void create("chart")}>📊 Chart</MenuItem>
        </Popover>
      )}
    </>
  );
}

export function DatabasePage({ page }: { page: Page }) {
  const workspaceId = page.workspace_id;
  const schema = useDatabase(workspaceId, page.id);
  const mutations = useDatabaseMutations(workspaceId, page.id);
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const canEdit = atLeast(page.my_role, "editor");

  const views = useMemo(
    () =>
      (schema.data?.views ?? [])
        .map((v) => ({ ...v, config: normalizeConfig(v.config) }))
        .sort((a, b) => a.position - b.position),
    [schema.data],
  );
  const properties = useMemo(
    () => [...(schema.data?.properties ?? [])].sort((a, b) => a.position - b.position),
    [schema.data],
  );
  const view = views.find((v) => v.id === params.get("view")) ?? views[0];
  const rows = useRows(workspaceId, page.id, view?.id ?? null, search.trim());
  const openRowId = params.get("row");
  const openRow = rows.data?.find((r) => r.id === openRowId);

  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  const setValue = useCallback(
    (row: Row, property: Property, value: unknown) => {
      if (OPTIMISTIC.has(property.type)) {
        qc.setQueriesData<Row[]>({ queryKey: ["rows", workspaceId, page.id] }, (old) =>
          old?.map((r) => (r.id === row.id ? { ...r, values: { ...r.values, [property.id]: value } } : r)),
        );
      }
      mutations.updateRow.mutate({ rowId: row.id, values: { [property.id]: value } });
    },
    [qc, workspaceId, page.id, mutations.updateRow],
  );

  const addOption = useCallback(
    async (property: Property, name: string): Promise<SelectOption> => {
      const options = property.config.options ?? [];
      const color = OPTION_COLORS[options.length % OPTION_COLORS.length] ?? "gray";
      const updated = await mutations.updateProperty.mutateAsync({
        propertyId: property.id,
        config: { ...property.config, options: [...options, { id: "", name, color }] },
      });
      return updated.config.options!.find((o) => o.name.toLowerCase() === name.toLowerCase())!;
    },
    [mutations.updateProperty],
  );

  const context: DatabaseContextValue = {
    workspaceId,
    databaseId: page.id,
    properties,
    canEdit,
    mutations,
    setValue,
    addOption,
    openRow: (id) => setParam("row", id),
  };

  if (schema.isLoading) return <p className="text-sm text-zinc-500">Loading database…</p>;
  if (schema.isError || !view) return <p className="text-sm text-zinc-500">Could not load this database.</p>;

  return (
    <DatabaseContext.Provider value={context}>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2 border-b border-zinc-200 dark:border-zinc-800">
        <div role="tablist" aria-label="Views" className="flex flex-wrap items-center">
          {views.map((v) => (
            <ViewTab key={v.id} view={v} active={v.id === view.id} canDelete={views.length > 1} onSelect={() => setParam("view", v.id)} />
          ))}
          {canEdit && <AddView properties={properties} onCreated={(id) => setParam("view", id)} />}
        </div>
        <div className="pb-1">
          <ViewToolbar view={view} search={search} onSearch={setSearch} />
        </div>
      </div>
      {rows.isError ? (
        <p className="text-sm text-zinc-500">Could not load the rows.</p>
      ) : view.type === "chart" ? (
        <ChartView key={view.id} view={view} />
      ) : view.type === "board" ? (
        <BoardView view={view} rows={rows.data ?? []} />
      ) : (
        <TableView view={view} rows={rows.data ?? []} />
      )}
      {openRow && <RowPanel key={openRow.id} row={openRow} onClose={() => setParam("row", null)} />}
    </DatabaseContext.Provider>
  );
}
