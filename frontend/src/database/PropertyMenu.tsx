/** Adding properties and editing one: name, type, options, relation and rollup settings. */
import { useQuery } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";

import { inputClass, MenuItem } from "@/components/Popover";
import { ApiError } from "@/lib/api";
import { fetchDatabase, usePages } from "@/lib/queries";
import type { DatabaseView, FilterOp, Property, PropertyConfig, PropertyType, RollupFilter, RollupFunction, SelectOption } from "@/lib/types";

import { useDatabaseContext } from "./context";
import {
  CONVERTIBLE,
  needsValue,
  OPTION_COLORS,
  optionsOf,
  PROPERTY_TYPES,
  propRef,
  rollupFunctionsFor,
  rollupOpsFor,
  rollupTargets,
  TYPE_ICON,
} from "./logic";

const label = "mb-1 block text-xs font-medium text-zinc-500";

function errorText(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.errors?.[0]?.message ?? error.message;
  return "Something went wrong.";
}

function useSchema(databaseId: string | undefined) {
  const { workspaceId } = useDatabaseContext();
  return useQuery({
    queryKey: ["database", workspaceId, databaseId],
    queryFn: () => fetchDatabase(workspaceId, databaseId!),
    enabled: !!databaseId,
  });
}

/** One step's filters: "only related rows where …". A value can be this row's property. */
function RollupFilters({
  title,
  filters,
  properties,
  onChange,
}: {
  title: string;
  filters: RollupFilter[];
  properties: Property[];
  onChange: (filters: RollupFilter[]) => void;
}) {
  const { properties: here } = useDatabaseContext();
  const set = (i: number, next: Partial<RollupFilter>) => onChange(filters.map((f, j) => (j === i ? { ...f, ...next } : f)));
  return (
    <div className="space-y-1">
      <span className={label}>{title}</span>
      {filters.map((filter, i) => {
        const prop = properties.find((p) => p.id === filter.property_id);
        const ops = prop ? rollupOpsFor(prop.type) : [];
        const isThis = typeof filter.value === "object" && filter.value !== null && "this" in (filter.value as object);
        const multi = filter.op === "in" || filter.op === "not_in";
        return (
          <div key={i} className="space-y-1 rounded-md border border-zinc-200 p-1.5 dark:border-zinc-800">
            <div className="flex gap-1">
              <select
                aria-label="Filter property"
                className={inputClass}
                value={filter.property_id}
                onChange={(e) => set(i, { property_id: e.target.value, op: rollupOpsFor(properties.find((p) => p.id === e.target.value)?.type ?? "text")[0]!.op, value: undefined })}
              >
                {properties.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <button type="button" aria-label="Remove filter" onClick={() => onChange(filters.filter((_, j) => j !== i))} className="px-1 text-zinc-400 hover:text-rose-600">
                ×
              </button>
            </div>
            <div className="flex gap-1">
              <select aria-label="Filter condition" className={inputClass} value={filter.op} onChange={(e) => set(i, { op: e.target.value as FilterOp, value: undefined })}>
                {ops.map((o) => (
                  <option key={o.op} value={o.op}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            {prop && needsValue(filter.op) && (
              <>
                {(prop.type === "select" || prop.type === "multi_select") && multi ? (
                  <div className="flex flex-wrap gap-2">
                    {optionsOf(prop).map((o) => {
                      const list = Array.isArray(filter.value) ? (filter.value as string[]) : [];
                      return (
                        <label key={o.id} className="flex items-center gap-1 text-xs">
                          <input
                            type="checkbox"
                            className="accent-brand-600"
                            checked={list.includes(o.id)}
                            onChange={(e) => set(i, { value: e.target.checked ? [...list, o.id] : list.filter((x) => x !== o.id) })}
                          />
                          {o.name}
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <select
                    aria-label="Filter value"
                    className={inputClass}
                    value={isThis ? `this:${(filter.value as { this: string }).this}` : prop.type === "select" || prop.type === "multi_select" ? String(filter.value ?? "") : "__text"}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (v.startsWith("this:")) set(i, { value: { this: v.slice(5) } });
                      else if (v === "__text") set(i, { value: "" });
                      else set(i, { value: v });
                    }}
                  >
                    {prop.type === "select" || prop.type === "multi_select" ? (
                      <>
                        <option value="">Choose…</option>
                        {optionsOf(prop).map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.name}
                          </option>
                        ))}
                      </>
                    ) : (
                      <option value="__text">A value…</option>
                    )}
                    <optgroup label="Or compare with this row">
                      {here.map((p) => (
                        <option key={p.id} value={`this:${p.id}`}>
                          This row&apos;s {p.name}
                        </option>
                      ))}
                    </optgroup>
                  </select>
                )}
                {!isThis && !multi && prop.type !== "select" && prop.type !== "multi_select" && (
                  <input
                    aria-label="Filter text"
                    className={inputClass}
                    type={prop.type === "number" ? "number" : prop.type === "date" || prop.type === "date_range" ? "date" : "text"}
                    value={String(filter.value ?? "")}
                    onChange={(e) => set(i, { value: prop.type === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value })}
                  />
                )}
              </>
            )}
          </div>
        );
      })}
      {properties.length > 0 && filters.length < 10 && (
        <button
          type="button"
          onClick={() => onChange([...filters, { property_id: properties[0]!.id, op: rollupOpsFor(properties[0]!.type)[0]!.op }])}
          className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-500"
        >
          + Add a filter
        </button>
      )}
    </div>
  );
}

/** Choose a relation (and optionally a second one), filters, and what to calculate. */
function RollupFields({ config, onChange }: { config: PropertyConfig; onChange: (config: PropertyConfig) => void }) {
  const { properties } = useDatabaseContext();
  const relations = properties.filter((p) => p.type === "relation");
  const relation = relations.find((p) => p.id === config.relation_property_id);
  const first = useSchema(relation?.config.database_id);
  const firstProps = first.data?.properties ?? [];
  const through = firstProps.find((p) => p.id === config.through_property_id && p.type === "relation");
  const second = useSchema(through?.config.database_id);
  const lastProps = through ? (second.data?.properties ?? []) : firstProps;
  const target = lastProps.find((p) => p.id === config.target_property_id);
  const functions = rollupFunctionsFor(target);
  if (!relations.length) return <p className="text-xs text-zinc-500">Add a relation first; a rollup summarises related rows.</p>;
  return (
    <div className="space-y-2">
      <label className="block">
        <span className={label}>Relation</span>
        <select
          className={inputClass}
          value={config.relation_property_id ?? ""}
          onChange={(e) => onChange({ function: config.function, relation_property_id: e.target.value })}
        >
          <option value="">Choose…</option>
          {relations.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      {relation && (
        <RollupFilters
          title="Only related rows where"
          filters={config.filters ?? []}
          properties={firstProps}
          onChange={(filters) => onChange({ ...config, filters })}
        />
      )}
      {relation && (
        <label className="block">
          <span className={label}>Then follow (optional)</span>
          <select
            className={inputClass}
            value={config.through_property_id ?? ""}
            disabled={!first.data}
            onChange={(e) => {
              const { through_filters: _f, weight_property_id: _w, target_property_id: _t, ...rest } = config;
              onChange({ ...rest, through_property_id: e.target.value || undefined });
            }}
          >
            <option value="">Stop here</option>
            {firstProps
              .filter((p) => p.type === "relation")
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
      )}
      {through && (
        <RollupFilters
          title={`Only ${through.name} rows where`}
          filters={config.through_filters ?? []}
          properties={lastProps}
          onChange={(through_filters) => onChange({ ...config, through_filters })}
        />
      )}
      <label className="block">
        <span className={label}>Property</span>
        <select
          className={inputClass}
          value={config.target_property_id ?? ""}
          disabled={!lastProps.length}
          onChange={(e) => onChange({ ...config, target_property_id: e.target.value })}
        >
          <option value="">Choose…</option>
          {rollupTargets(lastProps).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className={label}>Calculate</span>
        <select
          className={inputClass}
          value={config.function ?? "count"}
          onChange={(e) => onChange({ ...config, function: e.target.value as RollupFunction })}
        >
          {functions.map((f) => (
            <option key={f.fn} value={f.fn}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
      {config.function === "weighted_average" && (
        <label className="block">
          <span className={label}>Weighted by</span>
          <select className={inputClass} value={config.weight_property_id ?? ""} onChange={(e) => onChange({ ...config, weight_property_id: e.target.value })}>
            <option value="">Choose…</option>
            {lastProps.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}

/** Whether a rollup config has what the server needs to save it. */
function rollupReady(config: PropertyConfig): boolean {
  return !!config.relation_property_id && !!config.target_property_id && (config.function !== "weighted_average" || !!config.weight_property_id);
}

/** A formula: the expression, and the properties it can use (click to insert). */
function FormulaFields({ expression, onChange, exclude }: { expression: string; onChange: (expression: string) => void; exclude?: string }) {
  const { properties } = useDatabaseContext();
  const box = useRef<HTMLTextAreaElement>(null);
  const insert = (text: string) => {
    const el = box.current;
    if (!el) return onChange(expression + text);
    const at = el.selectionStart ?? expression.length;
    onChange(expression.slice(0, at) + text + expression.slice(el.selectionEnd ?? at));
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = at + text.length;
    });
  };
  return (
    <div className="space-y-1">
      <label className="block">
        <span className={label}>Formula</span>
        <textarea
          ref={box}
          rows={3}
          spellCheck={false}
          className={`${inputClass} font-mono text-xs`}
          value={expression}
          placeholder='prop("Score") / prop("Max Points") * 100'
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      <div className="flex flex-wrap gap-1">
        {properties
          .filter((p) => p.id !== exclude)
          .map((p) => (
            <button key={p.id} type="button" onClick={() => insert(propRef(p.name))} className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700">
              {p.name}
            </button>
          ))}
      </div>
      <p className="text-xs text-zinc-500">
        Functions: if, ifs, and, or, not, empty, round, floor, ceil, abs, min, max, sum, average, concat, length, contains, replace, format, toNumber, today, now,
        dateBetween, dateAdd, start, end, year, month, day.
      </p>
    </div>
  );
}

export function AddPropertyForm({ onDone }: { onDone: () => void }) {
  const { workspaceId, databaseId, mutations } = useDatabaseContext();
  const pages = usePages(workspaceId);
  const [name, setName] = useState("");
  const [type, setType] = useState<PropertyType>("text");
  const [config, setConfig] = useState<PropertyConfig>({});
  const [twoWay, setTwoWay] = useState(true);
  const databases = (pages.data ?? []).filter((p) => p.type === "database");
  const create = mutations.createProperty;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const fallback = PROPERTY_TYPES.find((t) => t.type === type)!.label;
    await create.mutateAsync({
      name: name.trim() || fallback,
      type,
      config,
      ...(type === "relation" ? { two_way: twoWay && config.database_id !== databaseId } : {}),
    });
    onDone();
  };

  return (
    <form onSubmit={submit} className="space-y-2 p-2">
      <label className="block">
        <span className={label}>Name</span>
        <input autoFocus className={inputClass} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Property name" />
      </label>
      <label className="block">
        <span className={label}>Type</span>
        <select
          className={inputClass}
          value={type}
          onChange={(e) => {
            setType(e.target.value as PropertyType);
            setConfig({});
          }}
        >
          {PROPERTY_TYPES.map((t) => (
            <option key={t.type} value={t.type}>
              {t.icon} {t.label}
            </option>
          ))}
        </select>
      </label>
      {type === "relation" && (
        <>
          <label className="block">
            <span className={label}>Related database</span>
            <select
              className={inputClass}
              required
              value={config.database_id ?? ""}
              onChange={(e) => setConfig({ database_id: e.target.value })}
            >
              <option value="">Choose…</option>
              {databases.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title || "Untitled"}
                  {d.id === databaseId ? " (this database)" : ""}
                </option>
              ))}
            </select>
          </label>
          {config.database_id && config.database_id !== databaseId && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={twoWay} onChange={(e) => setTwoWay(e.target.checked)} className="accent-brand-600" />
              Show on the related database too
            </label>
          )}
        </>
      )}
      {type === "rollup" && <RollupFields config={config} onChange={setConfig} />}
      {type === "formula" && <FormulaFields expression={config.expression ?? ""} onChange={(expression) => setConfig({ expression })} />}
      {create.error && <p className="text-xs text-rose-600">{errorText(create.error)}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onDone} className="px-2 text-sm text-zinc-500">
          Cancel
        </button>
        <button type="submit" disabled={create.isPending || (type === "rollup" && !rollupReady(config))} className="rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-on-brand hover:bg-brand-700 disabled:opacity-60">
          Add property
        </button>
      </div>
    </form>
  );
}

function OptionsEditor({ property }: { property: Property }) {
  const { mutations } = useDatabaseContext();
  const options = property.config.options ?? [];
  const [draft, setDraft] = useState("");
  const save = (next: SelectOption[]) =>
    mutations.updateProperty.mutate({ propertyId: property.id, config: { ...property.config, options: next } });

  return (
    <div className="space-y-1 px-2 py-1">
      <span className={label}>Options</span>
      {options.map((option, i) => (
        <div key={option.id} className="flex items-center gap-1">
          <select
            aria-label={`Color of ${option.name}`}
            value={option.color}
            onChange={(e) => save(options.map((o, j) => (j === i ? { ...o, color: e.target.value } : o)))}
            className="w-20 rounded border border-zinc-300 bg-transparent px-1 py-0.5 text-xs dark:border-zinc-700"
          >
            {OPTION_COLORS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <input
            aria-label="Option name"
            defaultValue={option.name}
            maxLength={100}
            onBlur={(e) => {
              const name = e.target.value.trim();
              if (name && name !== option.name) save(options.map((o, j) => (j === i ? { ...o, name } : o)));
            }}
            className={`${inputClass} py-0.5`}
          />
          <button
            type="button"
            aria-label={`Delete option ${option.name}`}
            onClick={() => save(options.filter((_, j) => j !== i))}
            className="px-1 text-zinc-400 hover:text-rose-600"
          >
            ×
          </button>
        </div>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!draft.trim()) return;
          save([...options, { id: "", name: draft.trim(), color: OPTION_COLORS[options.length % OPTION_COLORS.length] ?? "gray" }]);
          setDraft("");
        }}
      >
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add an option…" className={`${inputClass} py-0.5`} />
      </form>
    </div>
  );
}

/** The menu behind a column header. */
export function PropertyMenu({
  property,
  view,
  onClose,
}: {
  property: Property;
  view?: DatabaseView;
  onClose: () => void;
}) {
  const { workspaceId, canEdit, mutations } = useDatabaseContext();
  const pages = usePages(workspaceId);
  const [name, setName] = useState(property.name);
  const [rollup, setRollup] = useState<PropertyConfig>(property.config);
  const [expression, setExpression] = useState(property.config.expression ?? "");
  const update = mutations.updateProperty;

  const rename = () => {
    if (name.trim() && name.trim() !== property.name) update.mutate({ propertyId: property.id, name: name.trim() });
  };
  const sortBy = (direction: "asc" | "desc") => {
    if (!view) return;
    mutations.updateView.mutate({
      viewId: view.id,
      config: { ...view.config, sorts: [{ property_id: property.id, direction }] },
    });
    onClose();
  };
  const hide = () => {
    if (!view) return;
    mutations.updateView.mutate({ viewId: view.id, config: { ...view.config, hidden: [...view.config.hidden, property.id] } });
    onClose();
  };
  const target = pages.data?.find((p) => p.id === property.config.database_id);

  return (
    <div className="space-y-1">
      {canEdit ? (
        <div className="p-1">
          <input
            aria-label="Property name"
            className={inputClass}
            value={name}
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => e.key === "Enter" && rename()}
          />
        </div>
      ) : (
        <p className="px-2 py-1 font-medium">{property.name}</p>
      )}
      {canEdit && CONVERTIBLE.includes(property.type) && (
        <label className="block px-2 py-1">
          <span className={label}>Type</span>
          <select
            className={inputClass}
            value={property.type}
            onChange={(e) => {
              const type = e.target.value as PropertyType;
              if (window.confirm(`Change “${property.name}” to ${type.replace("_", "-")}? Values that do not fit are cleared.`))
                update.mutate({ propertyId: property.id, type });
            }}
          >
            {PROPERTY_TYPES.filter((t) => CONVERTIBLE.includes(t.type)).map((t) => (
              <option key={t.type} value={t.type}>
                {t.icon} {t.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {!CONVERTIBLE.includes(property.type) && (
        <p className="px-2 py-1 text-xs text-zinc-500">
          {TYPE_ICON[property.type]} {property.type === "title" ? "Title (every row has one)" : PROPERTY_TYPES.find((t) => t.type === property.type)?.label}
          {property.type === "relation" && (
            <>
              {" "}to <strong>{target ? target.title || "Untitled" : "a database you cannot see"}</strong>
              {(property.config.reverse_of || property.config.reverse_property_id) && " (two-way)"}
            </>
          )}
        </p>
      )}
      {canEdit && property.type === "number" && (
        <label className="block px-2 py-1">
          <span className={label}>Format</span>
          <select
            className={inputClass}
            value={property.config.format ?? "number"}
            onChange={(e) => update.mutate({ propertyId: property.id, config: { format: e.target.value as PropertyConfig["format"] } })}
          >
            <option value="number">Number</option>
            <option value="percent">Percent</option>
            <option value="currency">Currency (2 decimals)</option>
          </select>
        </label>
      )}
      {canEdit && (property.type === "select" || property.type === "multi_select") && <OptionsEditor property={property} />}
      {canEdit && property.type === "rollup" && (
        <div className="px-2 py-1">
          <RollupFields
            config={rollup}
            onChange={(next) => {
              setRollup(next);
              if (rollupReady(next)) update.mutate({ propertyId: property.id, config: next });
            }}
          />
        </div>
      )}
      {canEdit && property.type === "formula" && (
        <form
          className="space-y-1 px-2 py-1"
          onSubmit={(e) => {
            e.preventDefault();
            update.mutate({ propertyId: property.id, config: { expression } });
          }}
        >
          <FormulaFields expression={expression} onChange={setExpression} exclude={property.id} />
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={update.isPending || expression.trim() === (property.config.expression ?? "")}
              className="rounded-md bg-brand-600 px-3 py-1 text-xs font-medium text-on-brand hover:bg-brand-700 disabled:opacity-60"
            >
              Save formula
            </button>
          </div>
        </form>
      )}
      {!canEdit && property.type === "formula" && <p className="px-2 py-1 font-mono text-xs text-zinc-500">{property.config.expression}</p>}
      {update.error && <p className="px-2 text-xs text-rose-600">{errorText(update.error)}</p>}
      {view && canEdit && (
        <div className="border-t border-zinc-200 pt-1 dark:border-zinc-800">
          <MenuItem onClick={() => sortBy("asc")}>↑ Sort ascending</MenuItem>
          <MenuItem onClick={() => sortBy("desc")}>↓ Sort descending</MenuItem>
          {property.type !== "title" && <MenuItem onClick={hide}>Hide in this view</MenuItem>}
        </div>
      )}
      {canEdit && property.type !== "title" && (
        <div className="border-t border-zinc-200 pt-1 dark:border-zinc-800">
          <MenuItem
            danger
            onClick={() => {
              const extra = property.config.reverse_of || property.config.reverse_property_id ? " Its other half on the related database goes too." : "";
              if (!window.confirm(`Delete the “${property.name}” property and its values?${extra}`)) return;
              mutations.deleteProperty.mutate(property.id);
              onClose();
            }}
          >
            Delete property
          </MenuItem>
        </div>
      )}
    </div>
  );
}
