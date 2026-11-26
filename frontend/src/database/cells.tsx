/** Showing and editing one value of one row. Used by the table, board cards and row panel. */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { inputClass, Popover } from "@/components/Popover";
import { api } from "@/lib/api";
import { fetchDatabase, fetchRows, uploadFile, useMembers } from "@/lib/queries";
import type { DateRange, FileRef, Person, Property, RelationRef, Row, SelectOption, StoredFile } from "@/lib/types";

import { useDatabaseContext } from "./context";
import { COLOR_CLASS, COMPUTED, displayText, fileLabel, optionsOf, rowTitle } from "./logic";

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
            <span
              key={p.id ?? `name:${p.display_name}`}
              className={`${chip} ${p.id ? "" : "italic text-zinc-600 dark:text-zinc-400"}`}
              title={p.id ? undefined : "Not a member of this workspace (yet). Matched by name when they join."}
            >
              {p.display_name}
            </span>
          ))}
        </span>
      );
    case "file":
      return (
        <span className="flex flex-wrap gap-1">
          {(value as FileRef[]).map((f, i) => (
            <FileChip key={`${f.file_id ?? f.url}-${i}`} file={f} />
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
    case "formula":
      return (
        <span className={property.type === "number" || typeof value === "number" ? "block w-full text-right tabular-nums" : "truncate"}>
          {displayText(property, value)}
        </span>
      );
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
  const readOnly = !canEdit || COMPUTED.includes(property.type);

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
    case "date_range":
      return <DateRangePicker row={row} property={property} />;
    case "file":
      return <FilePicker row={row} property={property} />;
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
  const current = (row.values[property.id] as Person[] | null) ?? [];
  const selected = current.flatMap((p) => (p.id ? [p.id] : []));
  const names = current.filter((p) => !p.id).map((p) => p.display_name);
  // Written back as member ids plus {name} for people who are not members.
  const write = (ids: string[], keep: string[]) => setValue(row, property, [...ids, ...keep.map((name) => ({ name }))]);
  if (members.isError) return <p className="px-2 py-1 text-xs text-zinc-500">Only workspace members can pick people.</p>;
  const list = (members.data ?? []).filter((m) =>
    `${m.display_name} ${m.email}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const toggle = (id: string) => write(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id], names);
  const typed = query.trim();
  const addName = () => {
    if (!typed || list.length || names.some((n) => n.toLowerCase() === typed.toLowerCase())) return;
    write(selected, [...names, typed]);
    setQuery("");
  };
  return (
    <div>
      <SearchBox value={query} onChange={setQuery} placeholder="Search people, or type a name" onEnter={addName} />
      {names.map((name) => (
        <div key={name} className="flex items-center justify-between rounded-md px-2 py-1">
          <span className="truncate italic text-zinc-600 dark:text-zinc-400" title="Not a member: matched by name when they join">
            {name}
          </span>
          <button type="button" aria-label={`Remove ${name}`} onClick={() => write(selected, names.filter((n) => n !== name))} className="text-zinc-400 hover:text-rose-600">
            ×
          </button>
        </div>
      ))}
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
      {typed && !list.length && (
        <button type="button" onClick={addName} className="w-full rounded-md px-2 py-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800">
          Add <strong>{typed}</strong> by name
        </button>
      )}
    </div>
  );
}

function DateRangePicker({ row, property }: { row: Row; property: Property }) {
  const { setValue } = useDatabaseContext();
  const current = row.values[property.id] as DateRange | null;
  const [start, setStart] = useState(current?.start?.slice(0, 10) ?? "");
  const [end, setEnd] = useState(current?.end?.slice(0, 10) ?? "");
  const invalid = !!start && !!end && end < start;
  const save = () => {
    if (invalid) return;
    setValue(row, property, start || end ? { start: start || end, end: start && end ? end : null } : null);
  };
  return (
    <form
      className="space-y-2 p-1"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <label className="block text-xs text-zinc-500">
        Start
        <input type="date" aria-label="Start date" value={start} onChange={(e) => setStart(e.target.value)} className={inputClass} />
      </label>
      <label className="block text-xs text-zinc-500">
        End
        <input type="date" aria-label="End date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} className={inputClass} />
      </label>
      {invalid && <p className="text-xs text-rose-600">The end is before the start.</p>}
      <div className="flex justify-between">
        <button type="button" onClick={() => setValue(row, property, null)} className="text-xs text-zinc-500 hover:text-rose-600">
          Clear
        </button>
        <button type="submit" disabled={invalid} className="rounded-md bg-brand-600 px-3 py-1 text-xs font-medium text-on-brand hover:bg-brand-700 disabled:opacity-60">
          Save
        </button>
      </div>
    </form>
  );
}

/** A file in a file property: links open in a new tab; uploaded files get a fresh, short-lived URL. */
function FileChip({ file }: { file: FileRef }) {
  const { workspaceId, databaseId } = useDatabaseContext();
  const label = fileLabel(file);
  const web = !!file.url && /^https?:\/\//i.test(file.url);
  const open = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!file.file_id) return;
    const { file: stored } = await api<{ file: StoredFile }>(`/workspaces/${workspaceId}/pages/${databaseId}/files/${file.file_id}`);
    if (stored.url) window.open(stored.url, "_blank", "noopener,noreferrer");
  };
  if (web)
    return (
      <a href={file.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} className={`${chip} text-brand-600 underline underline-offset-2`} title={file.url}>
        📎 {label}
      </a>
    );
  if (file.file_id)
    return (
      <button type="button" onClick={(e) => void open(e)} className={`${chip} text-brand-600 underline underline-offset-2`}>
        📎 {label}
      </button>
    );
  // s3:// and similar paths are kept as references: show where the file lives.
  return (
    <span className={chip} title={file.url}>
      📎 {label}
    </span>
  );
}

function FilePicker({ row, property }: { row: Row; property: Property }) {
  const { workspaceId, databaseId, setValue } = useDatabaseContext();
  const files = (row.values[property.id] as FileRef[] | null) ?? [];
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const write = (next: FileRef[]) =>
    setValue(row, property, next.length ? next.map((f) => (f.file_id ? { file_id: f.file_id } : { url: f.url, name: f.name })) : null);
  const addLink = () => {
    const url = link.trim();
    if (!url) return;
    if (!/^(https?|s3|gs|minio|ftp|sftp):\/\/\S+$/i.test(url)) {
      setError("Links start with https://, s3://, gs:// or ftp://.");
      return;
    }
    setError(null);
    write([...files, { url, name: url.split("/").pop() || url }]);
    setLink("");
  };
  const upload = async (list: FileList | null) => {
    const picked = Array.from(list ?? []);
    if (!picked.length) return;
    setBusy(true);
    setError(null);
    try {
      const stored = await Promise.all(picked.map((f) => uploadFile(workspaceId, databaseId, f)));
      write([...files, ...stored.map((s) => ({ file_id: s.id, name: s.filename }))]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The upload failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2 p-1">
      {files.map((f, i) => (
        <div key={`${f.file_id ?? f.url}-${i}`} className="flex items-center justify-between gap-2">
          <FileChip file={f} />
          <button type="button" aria-label={`Remove ${fileLabel(f)}`} onClick={() => write(files.filter((_, j) => j !== i))} className="text-zinc-400 hover:text-rose-600">
            ×
          </button>
        </div>
      ))}
      <input
        value={link}
        onChange={(e) => setLink(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && addLink()}
        placeholder="Paste a link (https://, s3://…) and press Enter"
        aria-label="File link"
        className={inputClass}
      />
      <button type="button" disabled={busy} onClick={() => input.current?.click()} className="text-sm font-medium text-brand-600 hover:underline disabled:opacity-60 dark:text-brand-500">
        {busy ? "Uploading…" : "Upload a file"}
      </button>
      <input ref={input} type="file" multiple aria-label="Upload files" className="hidden" onChange={(e) => void upload(e.target.files)} />
      {error && <p className="text-xs text-rose-600">{error}</p>}
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
