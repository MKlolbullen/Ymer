/** Showing and editing one value of one row. Used by the table, board cards and row panel. */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { inputClass, Popover } from "@/components/Popover";
import { fetchDatabase, fetchRows, useMembers } from "@/lib/queries";
import type { Person, Property, RelationRef, Row, SelectOption } from "@/lib/types";

import { useDatabaseContext } from "./context";
import { COLOR_CLASS, displayText, optionsOf, rowTitle } from "./logic";

export function OptionChip({ option, onRemove }: { option: SelectOption; onRemove?: () => void }) {
  return (
    <span className={`inline-flex max-w-full items-center gap-1 truncate rounded px-1.5 py-0.5 text-xs ${COLOR_CLASS[option.color] ?? COLOR_CLASS.gray}`}>
      <span className="truncate">{option.name}</span>
      {onRemove && (
        <button type="button" aria-label={`Remove ${option.name}`} onClick={onRemove} className="opacity-60 hover:opacity-100">
          ×
        </button>
      )}
    </span>
  );
}

const chip = "inline-flex max-w-full items-center truncate rounded bg-zinc-100 px-1.5 py-0.5 text-xs dark:bg-zinc-800";

/** Read-only rendering of a value. */
export function CellValue({ property, value }: { property: Property; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  switch (property.type) {
    case "checkbox":
      return <span aria-label="checked">✓</span>;
    case "select": {
      const option = optionsOf(property).find((o) => o.id === value);
      return option ? <OptionChip option={option} /> : null;
    }
    case "multi_select": {
      const options = optionsOf(property);
      return (
        <span className="flex flex-wrap gap-1">
          {(value as string[]).map((id) => {
            const option = options.find((o) => o.id === id);
            return option ? <OptionChip key={id} option={option} /> : null;
          })}
        </span>
      );
    }
    case "person":
      return (
        <span className="flex flex-wrap gap-1">
          {(value as Person[]).map((p) => (
            <span key={p.id} className={chip}>
              {p.display_name}
            </span>
          ))}
        </span>
      );
    case "relation":
      return (
        <span className="flex flex-wrap gap-1">
          {(value as RelationRef[]).map((r) => (
            <span key={r.id} className={`${chip} ${r.title === null ? "italic text-zinc-500" : "underline decoration-zinc-300 underline-offset-2"}`}>
              ↗ {r.title === null ? "Restricted" : r.title || "Untitled"}
            </span>
          ))}
        </span>
      );
    case "url":
      return (
        <a
          href={String(value)}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="truncate text-brand-600 underline underline-offset-2"
        >
          {String(value).replace(/^https?:\/\//, "")}
        </a>
      );
    case "number":
    case "rollup":
      return <span className={property.type === "number" ? "block text-right tabular-nums" : "tabular-nums"}>{displayText(property, value)}</span>;
    default:
      return <span className="truncate">{displayText(property, value)}</span>;
  }
}

type CellProps = { row: Row; property: Property; variant?: "table" | "panel" };

/** A value that can be clicked to edit (when the user is an editor). */
export function Cell({ row, property, variant = "table" }: CellProps) {
  const { canEdit, setValue } = useDatabaseContext();
  const [editing, setEditing] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const value = row.values[property.id];
  const pad = variant === "table" ? "min-h-9 px-2 py-1.5" : "min-h-8 px-2 py-1";
  const readOnly = !canEdit || property.type === "rollup";

  if (property.type === "checkbox") {
    return (
      <div className={`flex items-center ${pad}`}>
        <input
          type="checkbox"
          aria-label={property.name}
          checked={value === true}
          disabled={!canEdit}
          onChange={(e) => setValue(row, property, e.target.checked)}
          className="size-4 accent-brand-600"
        />
      </div>
    );
  }

  const inline = ["title", "text", "number", "url", "date"].includes(property.type);
  if (editing && inline) {
    return <InlineEditor row={row} property={property} onDone={() => setEditing(false)} className={pad} />;
  }

  return (
    <>
      <div
        ref={ref}
        role={readOnly ? undefined : "button"}
        tabIndex={readOnly ? undefined : 0}
        aria-label={readOnly ? undefined : `Edit ${property.name}`}
        onClick={() => !readOnly && setEditing(true)}
        onKeyDown={(e) => {
          if (!readOnly && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            setEditing(true);
          }
        }}
        className={`flex items-center gap-1 overflow-hidden ${pad} ${readOnly ? "" : "cursor-text hover:bg-zinc-50 dark:hover:bg-zinc-900"} ${
          property.type === "title" ? "font-medium" : ""
        }`}
      >
        <CellValue property={property} value={value} />
        {variant === "panel" && (value === null || value === undefined) && <span className="text-zinc-400">Empty</span>}
      </div>
      {editing && !inline && (
        <Popover anchor={ref.current} onClose={() => setEditing(false)} label={`Edit ${property.name}`} width={280}>
          <PickerFor row={row} property={property} />
        </Popover>
      )}
    </>
  );
}

function InlineEditor({ row, property, onDone, className }: { row: Row; property: Property; onDone: () => void; className: string }) {
  const { setValue } = useDatabaseContext();
  const initial = row.values[property.id];
  const [text, setText] = useState(initial === null || initial === undefined ? "" : String(initial).slice(0, property.type === "date" ? 10 : undefined));
  const done = useRef(false);

  const commit = () => {
    if (done.current) return;
    done.current = true;
    const original = initial === null || initial === undefined ? "" : String(initial);
    if (text.trim() !== original.slice(0, property.type === "date" ? 10 : undefined)) {
      setValue(row, property, text.trim() === "" ? null : property.type === "number" ? Number(text.replace(",", ".")) : text);
    }
    onDone();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") {
      done.current = true;
      onDone();
    }
  };
  return (
    <div className={className}>
      <input
        autoFocus
        aria-label={property.name}
        type={property.type === "date" ? "date" : property.type === "number" ? "number" : "text"}
        step="any"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={onKeyDown}
        className="w-full bg-transparent outline-none"
      />
    </div>
  );
}

function PickerFor({ row, property }: { row: Row; property: Property }) {
  switch (property.type) {
    case "select":
    case "multi_select":
      return <OptionPicker row={row} property={property} />;
    case "person":
      return <PersonPicker row={row} property={property} />;
    case "relation":
      return <RelationPicker row={row} property={property} />;
    default:
      return null;
  }
}

function SearchBox({ value, onChange, placeholder, onEnter }: { value: string; onChange: (v: string) => void; placeholder: string; onEnter?: () => void }) {
  return (
    <input
      autoFocus
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => e.key === "Enter" && onEnter?.()}
      className={`${inputClass} mb-1`}
    />
  );
}

function OptionPicker({ row, property }: { row: Row; property: Property }) {
  const { setValue, addOption } = useDatabaseContext();
  const [query, setQuery] = useState("");
  const multi = property.type === "multi_select";
  const raw = row.values[property.id];
  const selected: string[] = multi ? ((raw as string[] | null) ?? []) : raw ? [raw as string] : [];
  const options = optionsOf(property);
  const matches = options.filter((o) => o.name.toLowerCase().includes(query.trim().toLowerCase()));
  const exact = options.some((o) => o.name.toLowerCase() === query.trim().toLowerCase());

  const toggle = (id: string) => {
    if (multi) setValue(row, property, selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
    else setValue(row, property, selected[0] === id ? null : id);
  };
  const create = async () => {
    const name = query.trim();
    if (!name || exact) return;
    const option = await addOption(property, name);
    setQuery("");
    setValue(row, property, multi ? [...selected, option.id] : option.id);
  };

  return (
    <div>
      <SearchBox value={query} onChange={setQuery} placeholder="Search or create an option" onEnter={() => void create()} />
      <ul role="listbox" aria-multiselectable={multi}>
        {matches.map((option) => (
          <li key={option.id}>
            <button
              type="button"
              role="option"
              aria-selected={selected.includes(option.id)}
              onClick={() => toggle(option.id)}
              className="flex w-full items-center justify-between rounded-md px-2 py-1 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <OptionChip option={option} />
              {selected.includes(option.id) && <span aria-hidden>✓</span>}
            </button>
          </li>
        ))}
      </ul>
      {query.trim() && !exact && (
        <button type="button" onClick={() => void create()} className="w-full rounded-md px-2 py-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800">
          Create <strong>{query.trim()}</strong>
        </button>
      )}
      {!options.length && !query && <p className="px-2 py-1 text-xs text-zinc-500">Type to create the first option.</p>}
    </div>
  );
}

function PersonPicker({ row, property }: { row: Row; property: Property }) {
  const { workspaceId, setValue } = useDatabaseContext();
  const members = useMembers(workspaceId);
  const [query, setQuery] = useState("");
  const selected = ((row.values[property.id] as Person[] | null) ?? []).map((p) => p.id);
  if (members.isError) return <p className="px-2 py-1 text-xs text-zinc-500">Only workspace members can pick people.</p>;
  const list = (members.data ?? []).filter((m) =>
    `${m.display_name} ${m.email}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const toggle = (id: string) =>
    setValue(row, property, selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
  return (
    <div>
      <SearchBox value={query} onChange={setQuery} placeholder="Search people" />
      {list.map((m) => (
        <button
          key={m.user_id}
          type="button"
          aria-pressed={selected.includes(m.user_id)}
          onClick={() => toggle(m.user_id)}
          className="flex w-full items-center justify-between rounded-md px-2 py-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <span className="truncate">
            {m.display_name} <span className="text-xs text-zinc-500">{m.email}</span>
          </span>
          {selected.includes(m.user_id) && <span aria-hidden>✓</span>}
        </button>
      ))}
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function RelationPicker({ row, property }: { row: Row; property: Property }) {
  const { workspaceId, setValue } = useDatabaseContext();
  const target = property.config.database_id ?? "";
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 200);
  const schema = useQuery({ queryKey: ["database", workspaceId, target], queryFn: () => fetchDatabase(workspaceId, target), enabled: !!target });
  const candidates = useQuery({
    queryKey: ["rows", workspaceId, target, "picker", q],
    queryFn: () => fetchRows(workspaceId, target, q),
    enabled: !!target,
  });
  const linked = (row.values[property.id] as RelationRef[] | null) ?? [];
  const ids = linked.map((r) => r.id);
  if (candidates.isError) return <p className="px-2 py-1 text-xs text-zinc-500">You cannot see the related database.</p>;
  const toggle = (id: string) => setValue(row, property, ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]);
  return (
    <div>
      <SearchBox value={query} onChange={setQuery} placeholder="Search rows" />
      {(candidates.data ?? []).slice(0, 50).map((candidate) => (
        <button
          key={candidate.id}
          type="button"
          aria-pressed={ids.includes(candidate.id)}
          onClick={() => toggle(candidate.id)}
          className="flex w-full items-center justify-between rounded-md px-2 py-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <span className="truncate">↗ {rowTitle(candidate, schema.data?.properties ?? []) || "Untitled"}</span>
          {ids.includes(candidate.id) && <span aria-hidden>✓</span>}
        </button>
      ))}
      {candidates.data?.length === 0 && <p className="px-2 py-1 text-xs text-zinc-500">No rows found.</p>}
    </div>
  );
}
