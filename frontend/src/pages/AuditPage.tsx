import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

import { useWorkspaceLayout } from "./workspace";

export type AuditEvent = {
  id: string;
  created_at: string;
  action: string;
  actor: { id: string; display_name: string; email: string } | null;
  target_type: string | null;
  target_id: string | null;
  ip: string | null;
  details: Record<string, unknown>;
};

const FILTERS: { label: string; prefix: string }[] = [
  { label: "Everything", prefix: "" },
  { label: "Members", prefix: "member." },
  { label: "Pages and sharing", prefix: "page." },
  { label: "Files", prefix: "file." },
  { label: "Factory", prefix: "factory." },
  { label: "Workspace", prefix: "workspace." },
];

const str = (v: unknown) => (v === null || v === undefined ? "none" : String(v));

/** One line in plain words for an audit event. */
export function describe(e: AuditEvent): string {
  const d = e.details;
  switch (e.action) {
    case "workspace.create":
      return "created the workspace";
    case "workspace.rename":
      return `renamed the workspace from “${str(d.from)}” to “${str(d.to)}”`;
    case "workspace.delete":
      return `deleted the workspace “${str(d.name)}”`;
    case "member.add":
      return `added ${str(d.email)} as ${str(d.role)}`;
    case "member.role":
      return `changed a member's role from ${str(d.from)} to ${str(d.to)}`;
    case "member.remove":
      return `removed a member (${str(d.role)})`;
    case "member.leave":
      return "left the workspace";
    case "page.delete":
      return `deleted the page “${str(d.title)}”` + (Number(d.pages) > 1 ? ` and ${Number(d.pages) - 1} page(s) under it` : "");
    case "page.share":
      return `shared a page with ${str(d.email)} as ${str(d.role)}`;
    case "page.unshare":
      return "stopped sharing a page with someone";
    case "file.upload":
      return `uploaded ${str(d.filename)}`;
    case "file.delete":
      return `deleted the file ${str(d.filename)}`;
    case "factory.build":
      return `started a Factory build (${str(d.sandbox)} sandbox)`;
    case "factory.release":
      return `made release ${str(d.release)}`;
    case "factory.push":
      return `pushed release ${str(d.release)} to ${str(d.remote)} (${str(d.branch)}): ${str(d.status)}`;
    case "factory.budget":
      return `changed the monthly Factory budget from ${str(d.from)} to ${str(d.to)} tokens`;
    default:
      return e.action;
  }
}

/** The workspace's audit log, for owners: who changed members, sharing, files and builds. */
export function AuditPage() {
  const { workspace, setHeader } = useWorkspaceLayout();
  const [prefix, setPrefix] = useState("");
  const isOwner = workspace.role === "owner";

  useEffect(() => {
    setHeader(<span className="text-sm text-zinc-500">Activity log</span>);
    return () => setHeader(null);
  }, [setHeader]);

  const log = useInfiniteQuery({
    queryKey: ["audit", workspace.id, prefix],
    enabled: isOwner,
    initialPageParam: "",
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "50" });
      if (pageParam) params.set("before", pageParam);
      if (prefix) params.set("action", prefix);
      return api<{ events: AuditEvent[]; more: boolean }>(`/workspaces/${workspace.id}/audit?${params}`);
    },
    getNextPageParam: (last) => (last.more ? last.events.at(-1)?.created_at : undefined),
  });

  if (!isOwner) {
    return <p className="text-sm text-zinc-500">Only workspace owners can see the activity log.</p>;
  }
  const events = log.data?.pages.flatMap((p) => p.events) ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Activity log</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Changes to members, sharing, files and Factory builds in {workspace.name}. Kept for a year.
        </p>
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter">
        {FILTERS.map((f) => (
          <button
            key={f.prefix}
            onClick={() => setPrefix(f.prefix)}
            aria-pressed={prefix === f.prefix}
            className={`rounded-full border px-3 py-1 text-xs ${
              prefix === f.prefix
                ? "border-brand-500 bg-brand-50 text-brand-700 dark:bg-zinc-800 dark:text-brand-500"
                : "border-zinc-300 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      {log.isError && <p role="alert" className="text-sm text-rose-600">Could not load the activity log.</p>}
      {log.isPending ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : events.length === 0 ? (
        <p className="text-sm text-zinc-500">Nothing here yet.</p>
      ) : (
        <ol className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {events.map((e) => (
            <li key={e.id} className="flex flex-col gap-0.5 px-4 py-2.5 text-sm sm:flex-row sm:items-baseline sm:gap-3">
              <time className="shrink-0 text-xs text-zinc-500 tabular-nums" dateTime={e.created_at}>
                {new Date(e.created_at).toLocaleString()}
              </time>
              <span className="min-w-0">
                <strong className="font-medium">{e.actor?.display_name ?? "Someone"}</strong> {describe(e)}
                {e.ip && <span className="ml-2 text-xs text-zinc-400">from {e.ip}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
      {log.hasNextPage && (
        <button
          onClick={() => void log.fetchNextPage()}
          disabled={log.isFetchingNextPage}
          className="text-sm text-brand-600 hover:underline disabled:opacity-50"
        >
          {log.isFetchingNextPage ? "Loading…" : "Show older"}
        </button>
      )}
    </div>
  );
}
