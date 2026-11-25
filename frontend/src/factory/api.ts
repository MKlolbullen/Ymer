/** The Factory's API: sources, the brief, its questions and approval (stage F1). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { keys as baseKeys } from "@/lib/queries";
import type { Page, PageType, Person } from "@/lib/types";

export type FactoryStage = "sources" | "brief" | "approved" | "architecture" | "planned";

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
};

/** Brief approved, so the architecture step is open (or already done). */
export const briefApproved = (f: Factory) => f.stage === "approved" || f.stage === "architecture" || f.stage === "planned";

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
        api<{ factory: Factory }>(`${url}/sources`, { method: "PUT", json: { page_ids: pageIds } }),
      ...opts,
    }),
    writeBrief: useMutation({
      mutationFn: () => api<{ factory: Factory; demo: boolean }>(`${url}/brief`, { method: "POST" }),
      onSuccess: (r: { factory: Factory }) => {
        settle(r.factory);
        // The brief is a new page under the factory; an earlier one was renamed.
        void qc.invalidateQueries({ queryKey: baseKeys.pages(workspaceId) });
      },
    }),
    answer: useMutation({
      mutationFn: ({ id, ...body }: { id: string; answer?: string; status?: "open" | "dismissed" }) =>
        api<{ factory: Factory }>(`${url}/questions/${id}`, { method: "PATCH", json: body }),
      ...opts,
    }),
    designArchitecture: useMutation({
      mutationFn: (stack: string | null) =>
        api<{ factory: Factory; demo: boolean }>(`${url}/architecture`, { method: "POST", json: { stack } }),
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
  };
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
  const done = f.stage === "planned" ? 2 : briefApproved(f) ? 1 : 0;
  return [0, 1, 2, 3, 4].map((i) =>
    i < done ? "done" : i === done ? (done >= 2 ? "next" : "current") : "later",
  );
}
