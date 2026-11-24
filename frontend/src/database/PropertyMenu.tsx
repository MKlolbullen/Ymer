/** Adding properties and editing one: name, type, options, relation and rollup settings. */
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { inputClass, MenuItem } from "@/components/Popover";
import { ApiError } from "@/lib/api";
import { fetchDatabase, usePages } from "@/lib/queries";
import type { DatabaseView, Property, PropertyConfig, PropertyType, RollupFunction, SelectOption } from "@/lib/types";

import { useDatabaseContext } from "./context";
import { CONVERTIBLE, OPTION_COLORS, PROPERTY_TYPES, ROLLUP_FUNCTIONS, rollupTargets, TYPE_ICON } from "./logic";

const label = "mb-1 block text-xs font-medium text-zinc-500";

function errorText(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.errors?.[0]?.message ?? error.message;
  return "Something went wrong.";
}

/** Choose a relation and a property of the related database to roll up. */
function RollupFields({ config, onChange }: { config: PropertyConfig; onChange: (config: PropertyConfig) => void }) {
  const { workspaceId, properties } = useDatabaseContext();
  const relations = properties.filter((p) => p.type === "relation");
  const relation = relations.find((p) => p.id === config.relation_property_id);
  const target = relation?.config.database_id;
  const related = useQuery({
    queryKey: ["database", workspaceId, target],
    queryFn: () => fetchDatabase(workspaceId, target!),
    enabled: !!target,
  });
  if (!relations.length) return <p className="text-xs text-zinc-500">Add a relation first; a rollup summarises related rows.</p>;
  return (
    <div className="space-y-2">
      <label className="block">
        <span className={label}>Relation</span>
        <select
          className={inputClass}
          value={config.relation_property_id ?? ""}
          onChange={(e) => onChange({ ...config, relation_property_id: e.target.value, target_property_id: undefined })}
        >
          <option value="">Choose…</option>
          {relations.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className={label}>Property</span>
        <select
          className={inputClass}
          value={config.target_property_id ?? ""}
          disabled={!related.data}
          onChange={(e) => onChange({ ...config, target_property_id: e.target.value })}
        >
          <option value="">Choose…</option>
          {rollupTargets(related.data?.properties ?? []).map((p) => (
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
          {ROLLUP_FUNCTIONS.map((f) => (
            <option key={f.fn} value={f.fn}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
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
      {create.error && <p className="text-xs text-rose-600">{errorText(create.error)}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onDone} className="px-2 text-sm text-zinc-500">
          Cancel
        </button>
        <button type="submit" disabled={create.isPending} className="rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60">
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
              if (next.relation_property_id && next.target_property_id) update.mutate({ propertyId: property.id, config: next });
            }}
          />
        </div>
      )}
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
