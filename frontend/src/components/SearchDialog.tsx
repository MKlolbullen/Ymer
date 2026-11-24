import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { useSearch } from "@/lib/queries";
import type { SearchResult } from "@/lib/types";

const ICON: Record<string, string> = { document: "📄", folder: "📁", database: "🗃️", mindmap: "🧠", row: "▤" };

/** Turn the server's \u0002match\u0003 markers into <mark> elements (no HTML involved). */
export function highlight(snippet: string): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = snippet;
  let key = 0;
  while (rest) {
    const start = rest.indexOf("\u0002");
    if (start < 0) {
      out.push(rest);
      break;
    }
    const end = rest.indexOf("\u0003", start);
    if (start > 0) out.push(rest.slice(0, start));
    const hit = rest.slice(start + 1, end < 0 ? undefined : end);
    out.push(
      <mark key={key++} className="search-hit">
        {hit}
      </mark>,
    );
    rest = end < 0 ? "" : rest.slice(end + 1);
  }
  return out;
}

export function resultPath(workspaceId: string, result: SearchResult): string {
  const base = `/w/${workspaceId}/p/${result.page_id}`;
  return result.kind === "row" ? `${base}?row=${result.row_id}` : base;
}

function useDebounced(value: string, ms: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/** Workspace search (Ctrl/Cmd+K): page titles, document text and database rows. */
export function SearchDialog({ workspaceId, onClose }: { workspaceId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 150);
  const search = useSearch(workspaceId, q);
  const results = q ? (search.data ?? []) : [];
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [search.data]);

  const open = (result: SearchResult | undefined) => {
    if (!result) return;
    navigate(resultPath(workspaceId, result));
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[12vh]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label="Search"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-xl overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900"
      >
        <input
          autoFocus
          type="search"
          aria-label="Search pages and rows"
          placeholder="Search pages, documents and database rows…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => Math.min(i + 1, results.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(i - 1, 0));
            }
            if (e.key === "Enter") open(results[index]);
          }}
          className="w-full border-b border-zinc-200 bg-transparent px-4 py-3 text-base focus:outline-none dark:border-zinc-700"
        />
        <ul role="listbox" aria-label="Results" className="max-h-[55vh] overflow-y-auto p-1">
          {results.map((result, i) => (
            <li key={`${result.kind}-${result.row_id ?? result.page_id}`}>
              <button
                type="button"
                role="option"
                aria-selected={i === index}
                onMouseEnter={() => setIndex(i)}
                onClick={() => open(result)}
                className={`flex w-full flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left ${i === index ? "bg-zinc-100 dark:bg-zinc-800" : ""}`}
              >
                <span className="flex w-full items-center gap-2 text-sm font-medium">
                  <span aria-hidden>{ICON[result.type] ?? "📄"}</span>
                  <span className="truncate">{result.title || "Untitled"}</span>
                  {result.kind === "row" && (
                    <span className="ml-auto shrink-0 text-xs font-normal text-zinc-500">in {result.database_title || "Untitled"}</span>
                  )}
                </span>
                {result.snippet && <span className="line-clamp-2 text-xs text-zinc-500">{highlight(result.snippet)}</span>}
              </button>
            </li>
          ))}
        </ul>
        {q && !search.isFetching && results.length === 0 && <p className="px-4 py-3 text-sm text-zinc-500">Nothing found for “{q}”.</p>}
        {!q && <p className="px-4 py-3 text-xs text-zinc-500">Type to search. Words match as prefixes; ↑ ↓ to move, Enter to open.</p>}
      </div>
    </div>
  );
}
