import { useEffect, useState, type ReactNode } from "react";
import { Search, Sparkles } from "lucide-react";
import { useNavigate } from "react-router";

import { useAiStatus, useSemanticSearch } from "@/ai/api";
import { useSearch } from "@/lib/queries";
import type { SearchResult } from "@/lib/types";

import { Icon, PageIcon } from "./icons";

type Item =
  | { kind: "ask"; key: string }
  | { kind: "result"; key: string; result: SearchResult; related: boolean };

const resultKey = (r: SearchResult) => `${r.kind}-${r.row_id ?? r.page_id}`;

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

/**
 * Workspace search (Ctrl/Cmd+K): page titles, document text and database rows by
 * keyword, then (when the AI layer is on) related pages found by meaning, and an
 * "Ask AI" shortcut that opens the question in the Q&A page.
 */
export function SearchDialog({ workspaceId, onClose }: { workspaceId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 150);
  const search = useSearch(workspaceId, q);
  const ai = useAiStatus();
  const semantic = useSemanticSearch(workspaceId, q, !!ai.data?.semantic_search);
  const keyword = q ? (search.data ?? []) : [];
  const seen = new Set(keyword.map(resultKey));
  const related = q ? (semantic.data ?? []).filter((r) => !seen.has(resultKey(r))) : [];
  const items: Item[] = [
    ...(q && ai.data?.enabled ? [{ kind: "ask" as const, key: "ask" }] : []),
    ...keyword.map((result) => ({ kind: "result" as const, key: resultKey(result), result, related: false })),
    ...related.map((result) => ({ kind: "result" as const, key: `related-${resultKey(result)}`, result, related: true })),
  ];
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [search.data, semantic.data]);

  const open = (item: Item | undefined) => {
    if (!item) return;
    if (item.kind === "ask") navigate(`/w/${workspaceId}/ask?q=${encodeURIComponent(q)}`);
    else navigate(resultPath(workspaceId, item.result));
    onClose();
  };
  const firstRelated = items.findIndex((i) => i.kind === "result" && i.related);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[12vh]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label="Search"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-xl overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900"
      >
        <div className="flex items-center gap-2 border-b border-zinc-200 px-4 dark:border-zinc-700">
        <Icon icon={Search} size={18} className="text-zinc-400" />
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
              setIndex((i) => Math.min(i + 1, items.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(i - 1, 0));
            }
            if (e.key === "Enter") open(items[index]);
          }}
          className="w-full bg-transparent py-3 text-base focus:outline-none"
        />
        </div>
        <ul role="listbox" aria-label="Results" className="max-h-[55vh] overflow-y-auto p-1">
          {items.map((item, i) => (
            <li key={item.key}>
              {i === firstRelated && (
                <p className="px-3 pt-2 pb-1 text-xs font-medium tracking-wide text-zinc-500 uppercase">Related by meaning</p>
              )}
              <button
                type="button"
                role="option"
                aria-selected={i === index}
                onMouseEnter={() => setIndex(i)}
                onClick={() => open(item)}
                className={`flex w-full flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left ${i === index ? "bg-zinc-100 dark:bg-zinc-800" : ""}`}
              >
                {item.kind === "ask" ? (
                  <span className="flex w-full items-center gap-2 text-sm font-medium text-brand-600 dark:text-brand-500">
                    <Icon icon={Sparkles} />
                    <span className="truncate">Ask AI: “{q}”</span>
                  </span>
                ) : (
                  <>
                    <span className="flex w-full items-center gap-2 text-sm font-medium">
                      <PageIcon type={item.result.type} className="text-zinc-500" />
                      <span className="truncate">{item.result.title || "Untitled"}</span>
                      {item.result.kind === "row" && (
                        <span className="ml-auto shrink-0 text-xs font-normal text-zinc-500">
                          in {item.result.database_title || "Untitled"}
                        </span>
                      )}
                    </span>
                    {item.result.snippet && (
                      <span className="line-clamp-2 text-xs text-zinc-500">{highlight(item.result.snippet)}</span>
                    )}
                  </>
                )}
              </button>
            </li>
          ))}
        </ul>
        {q && !search.isFetching && !semantic.isFetching && keyword.length === 0 && related.length === 0 && (
          <p className="px-4 py-3 text-sm text-zinc-500">Nothing found for “{q}”.</p>
        )}
        {!q && <p className="px-4 py-3 text-xs text-zinc-500">Type to search. Words match as prefixes; ↑ ↓ to move, Enter to open.</p>}
      </div>
    </div>
  );
}
