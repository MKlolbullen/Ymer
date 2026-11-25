/** The Factory's API: the brief (F1), the architecture (F2), the build (F3) and delivery (F4). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { keys as baseKeys } from "@/lib/queries";
import type { Page, PageType, Person } from "@/lib/types";

export type FactoryStage = "sources" | "brief" | "approved" | "architecture" | "planned" | "built" | "released";

export type RunStatus = "queued" | "running" | "stopping" | "passed" | "failed" | "stopped";

export type FactoryRun = {
  id: string;
  status: RunStatus;
  started_by: Person | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  steps: number;
  tokens: number;
  tasks_done: number;
  tasks_total: number;
  summary: string | null;
  error: string | null;
  sandbox: string | null;
  model: string | null;
};

export type RunEvent = {
  seq: number;
  kind: "start" | "task" | "note" | "file" | "tests" | "command" | "done" | "blocked" | "finish" | string;
  title: string;
  detail: string | null;
  at: string;
};

export type FactoryFile = { path: string; size: number; updated_at: string };

export type Changes = { added: string[]; changed: string[]; removed: string[] };

export type FactoryPush = {
  id: string;
  remote: string;
  branch: string;
  status: "pushed" | "unchanged" | "failed";
  commit_sha: string | null;
  error: string | null;
  pushed_by: Person | null;
  created_at: string;
};

export type FactoryRelease = {
  id: string;
  number: number;
  run_id: string | null;
  stack: string;
  notes: string | null;
  file_count: number;
  size: number;
  changes: Changes;
  approved_by: Person | null;
  created_at: string;
  preview: boolean;
  pushes: FactoryPush[];
};

export type Delivery = {
  latest_release: FactoryRelease | null;
  release_count: number;
  unreleased: Changes | null;
  git_remote: string | null;
  git_branch: string | null;
  git_hosts: string[];
};

export type Stack = {
  key: string;
  name: string;
  summary: string;
  good_for: string;
  languages: string;
  test_command: string;
  delivers: string;
};

export type FactorySource =
  | { page_id: string; title: string; type: PageType; visible: true }
  | { page_id: null; title: null; type: null; visible: false };

export type FactoryQuestion = {
  id: string;
  question: string;
  reason: string;
  status: "open" | "answered" | "dismissed";
  answer: string | null;
  answered_by: Person | null;
  answered_at: string | null;
};

export type Factory = {
  page_id: string;
  stage: FactoryStage;
  can_edit: boolean;
  sources: FactorySource[];
  questions: FactoryQuestion[];
  brief: Page | null;
  brief_generated_at: string | null;
  approved_at: string | null;
  approved_by: Person | null;
  stack: string | null;
  stacks: Stack[];
  architecture: Page | null;
  system_map: Page | null;
  tasks: (Page & { count: number }) | null;
  architecture_generated_at: string | null;
  plan_approved_at: string | null;
  plan_approved_by: Person | null;
  build: {
    latest_run: FactoryRun | null;
    file_count: number;
    sandbox: string | null;
  };
  delivery: Delivery;
};

/** Brief approved, so the architecture step is open (or already done). */
export const briefApproved = (f: Factory) =>
  f.stage === "approved" ||
  f.stage === "architecture" ||
  f.stage === "planned" ||
  f.stage === "built" ||
  f.stage === "released";

/** The plan is approved, so builds can start. */
export const canBuild = (f: Factory) => f.stage === "planned" || f.stage === "built" || f.stage === "released";

/** Files changed since the last release (or since nothing was released). */
export const changeCount = (c: Changes | null | undefined) =>
  c ? c.added.length + c.changed.length + c.removed.length : 0;

/** A passed build with changes not yet released: ready for review. */
export function readyForReview(f: Factory): boolean {
  const run = f.build.latest_run;
  return !!run && run.status === "passed" && changeCount(f.delivery.unreleased) > 0;
}

export const runActive = (run: FactoryRun | null | undefined) =>
  !!run && (run.status === "queued" || run.status === "running" || run.status === "stopping");

const base = (w: string, p: string) => `/workspaces/${w}/factories/${p}`;
export const factoryKey = (w: string, p: string) => ["factory", w, p] as const;

export function useFactory(workspaceId: string, pageId: string) {
  return useQuery({
    queryKey: factoryKey(workspaceId, pageId),
    queryFn: () => api<{ factory: Factory }>(base(workspaceId, pageId)).then((r) => r.factory),
  });
}

/** Every change answers with the whole factory, which replaces the cached copy. */
export function useFactoryMutations(workspaceId: string, pageId: string) {
  const qc = useQueryClient();
  const url = base(workspaceId, pageId);
  const settle = (factory: Factory) => qc.setQueryData(factoryKey(workspaceId, pageId), factory);
  const opts = { onSuccess: (r: { factory: Factory }) => settle(r.factory) };

  return {
    setSources: useMutation({
      mutationFn: (pageIds: string[]) =>
        api<{ factory: Factory }>(`${url}/sources`, {
          method: "PUT",
          json: { page_ids: pageIds },
        }),
      ...opts,
    }),
    writeBrief: useMutation({
      mutationFn: () =>
        api<{ factory: Factory; demo: boolean }>(`${url}/brief`, {
          method: "POST",
        }),
      onSuccess: (r: { factory: Factory }) => {
        settle(r.factory);
        // The brief is a new page under the factory; an earlier one was renamed.
        void qc.invalidateQueries({ queryKey: baseKeys.pages(workspaceId) });
      },
    }),
    answer: useMutation({
      mutationFn: ({ id, ...body }: { id: string; answer?: string; status?: "open" | "dismissed" }) =>
        api<{ factory: Factory }>(`${url}/questions/${id}`, {
          method: "PATCH",
          json: body,
        }),
      ...opts,
    }),
    designArchitecture: useMutation({
      mutationFn: (stack: string | null) =>
        api<{ factory: Factory; demo: boolean }>(`${url}/architecture`, {
          method: "POST",
          json: { stack },
        }),
      onSuccess: (r: { factory: Factory }) => {
        settle(r.factory);
        void qc.invalidateQueries({ queryKey: baseKeys.pages(workspaceId) });
      },
    }),
    approvePlan: useMutation({
      mutationFn: () => api<{ factory: Factory }>(`${url}/approve-plan`, { method: "POST" }),
      ...opts,
    }),
    approve: useMutation({
      mutationFn: () => api<{ factory: Factory }>(`${url}/approve`, { method: "POST" }),
      ...opts,
    }),
    reopen: useMutation({
      mutationFn: () => api<{ factory: Factory }>(`${url}/reopen`, { method: "POST" }),
      ...opts,
    }),
    startRun: useMutation({
      mutationFn: () => api<{ run: FactoryRun }>(`${url}/runs`, { method: "POST" }),
      onSuccess: () =>
        void qc.invalidateQueries({
          queryKey: factoryKey(workspaceId, pageId),
        }),
    }),
    release: useMutation({
      mutationFn: (notes: string) =>
        api<{ release: FactoryRelease; factory: Factory }>(`${url}/releases`, { method: "POST", json: { notes } }),
      onSuccess: (r: { factory: Factory }) => {
        settle(r.factory);
        void qc.invalidateQueries({ queryKey: ["factory-releases", workspaceId, pageId] });
      },
    }),
    push: useMutation({
      mutationFn: ({ number, ...body }: { number: number; remote: string; branch: string; token: string }) =>
        api<{ push: FactoryPush; release: FactoryRelease }>(`${url}/releases/${number}/push`, {
          method: "POST",
          json: body,
        }),
      onSettled: () => {
        void qc.invalidateQueries({ queryKey: factoryKey(workspaceId, pageId) });
        void qc.invalidateQueries({ queryKey: ["factory-releases", workspaceId, pageId] });
      },
    }),
    stopRun: useMutation({
      mutationFn: (runId: string) =>
        api<{ run: FactoryRun }>(`${url}/runs/${runId}/stop`, {
          method: "POST",
        }),
      onSuccess: () =>
        void qc.invalidateQueries({
          queryKey: factoryKey(workspaceId, pageId),
        }),
    }),
  };
}

/** Add newly fetched log lines, skipping any already shown. */
export function mergeEvents(previous: RunEvent[], next: RunEvent[]): RunEvent[] {
  const last = previous.at(-1)?.seq ?? 0;
  const fresh = next.filter((e) => e.seq > last);
  return fresh.length ? [...previous, ...fresh] : previous;
}

const POLL_MS = 1500;

/**
 * Follow one build: its status and its log. While the build is active the log is
 * fetched every 1.5 s, asking only for lines after the last one shown. When it ends,
 * the factory (stage, file count) and the Tasks database are refreshed.
 */
export function useRunLog(workspaceId: string, pageId: string, runId: string | null) {
  const qc = useQueryClient();
  const [run, setRun] = useState<FactoryRun | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const lastSeq = useRef(0);

  useEffect(() => {
    setRun(null);
    setEvents([]);
    lastSeq.current = 0;
    if (!runId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const r = await api<{ run: FactoryRun; events: RunEvent[] }>(
          `${base(workspaceId, pageId)}/runs/${runId}?after=${lastSeq.current}`,
        );
        if (stopped) return;
        setRun(r.run);
        const latest = r.events.at(-1);
        if (latest) {
          lastSeq.current = latest.seq;
          setEvents((prev) => mergeEvents(prev, r.events));
        }
        if (runActive(r.run) || r.events.length) {
          timer = setTimeout(poll, runActive(r.run) ? POLL_MS : 0);
        } else {
          void qc.invalidateQueries({
            queryKey: factoryKey(workspaceId, pageId),
          });
          void qc.invalidateQueries({
            queryKey: ["factory-files", workspaceId, pageId],
          });
          void qc.invalidateQueries({ queryKey: ["rows", workspaceId] });
        }
      } catch {
        if (!stopped) timer = setTimeout(poll, POLL_MS * 2);
      }
    };
    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [workspaceId, pageId, runId, qc]);

  return { run, events };
}

export function useFactoryFiles(workspaceId: string, pageId: string, enabled: boolean, version: string) {
  return useQuery({
    queryKey: ["factory-files", workspaceId, pageId, version],
    queryFn: () => api<{ files: FactoryFile[] }>(`${base(workspaceId, pageId)}/files`).then((r) => r.files),
    enabled,
  });
}

export function useFactoryFile(workspaceId: string, pageId: string, path: string | null, version: string) {
  return useQuery({
    queryKey: ["factory-files", workspaceId, pageId, version, path],
    queryFn: () =>
      api<{ content: string }>(`${base(workspaceId, pageId)}/files/content?path=${encodeURIComponent(path!)}`).then(
        (r) => r.content,
      ),
    enabled: !!path,
  });
}

/** Answers given after the brief was written: the brief does not include them yet. */
export function newAnswers(factory: Factory): number {
  if (!factory.brief_generated_at) return 0;
  const written = new Date(factory.brief_generated_at).getTime();
  return factory.questions.filter(
    (q) => q.status === "answered" && q.answered_at && new Date(q.answered_at).getTime() > written,
  ).length;
}

/** Pages that can be a source: not folders or factories, and not the factory's own brief. */
export function candidateSources(pages: Page[], factory: Factory, query: string): Page[] {
  const chosen = new Set(factory.sources.map((s) => s.page_id));
  const q = query.trim().toLowerCase();
  return pages
    .filter(
      (p) =>
        p.type !== "folder" &&
        p.type !== "factory" &&
        p.id !== factory.brief?.id &&
        p.parent_id !== factory.page_id &&
        !chosen.has(p.id) &&
        (!q || p.title.toLowerCase().includes(q)),
    )
    .slice(0, 50);
}

export type StepState = "done" | "current" | "next" | "later";

/** Brief, Architecture, Build, Review, Deliver: which are done, current or coming next. */
export function stepStates(f: Factory): StepState[] {
  const done =
    f.stage === "released" ? 4 : f.stage === "built" ? 3 : f.stage === "planned" ? 2 : briefApproved(f) ? 1 : 0;
  return [0, 1, 2, 3, 4].map((i) => (i < done ? "done" : i === done ? "current" : "later"));
}

export function useReleases(workspaceId: string, pageId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["factory-releases", workspaceId, pageId],
    queryFn: () =>
      api<{ releases: FactoryRelease[] }>(`${base(workspaceId, pageId)}/releases`).then((r) => r.releases),
    enabled,
  });
}

export function previewLink(workspaceId: string, pageId: string, number: number) {
  return api<{ url: string; expires_in: number }>(`${base(workspaceId, pageId)}/releases/${number}/preview`, {
    method: "POST",
  });
}

export const releaseDownloadPath = (workspaceId: string, pageId: string, number: number) =>
  `${base(workspaceId, pageId)}/releases/${number}/download`;
