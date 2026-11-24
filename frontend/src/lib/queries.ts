/** Server state (React Query). Keys start with the workspace id so a switch is clean. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";
import type { Comment, CommentAnchor, Page, PageType, StoredFile, Version, Workspace } from "./types";

const ws = (id: string) => `/workspaces/${id}`;
const pg = (workspaceId: string, pageId: string) => `${ws(workspaceId)}/pages/${pageId}`;

export const keys = {
  workspaces: ["workspaces"] as const,
  pages: (w: string) => ["pages", w] as const,
  page: (w: string, p: string) => ["pages", w, p] as const,
  comments: (w: string, p: string) => ["comments", w, p] as const,
  versions: (w: string, p: string) => ["versions", w, p] as const,
  file: (w: string, p: string, f: string) => ["file", w, p, f] as const,
};

export function useWorkspaces() {
  return useQuery({
    queryKey: keys.workspaces,
    queryFn: () => api<{ workspaces: Workspace[] }>("/workspaces").then((r) => r.workspaces),
  });
}

export function useCreateWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<{ workspace: Workspace }>("/workspaces", { method: "POST", json: { name } }).then(
        (r) => r.workspace,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.workspaces }),
  });
}

export function usePages(workspaceId: string) {
  return useQuery({
    queryKey: keys.pages(workspaceId),
    queryFn: () => api<{ pages: Page[] }>(`${ws(workspaceId)}/pages`).then((r) => r.pages),
  });
}

export function usePage(workspaceId: string, pageId: string) {
  return useQuery({
    queryKey: keys.page(workspaceId, pageId),
    queryFn: () => api<{ page: Page }>(pg(workspaceId, pageId)).then((r) => r.page),
  });
}

export function useCreatePage(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { type: PageType; title?: string; parent_id?: string | null }) =>
      api<{ page: Page }>(`${ws(workspaceId)}/pages`, { method: "POST", json: body }).then(
        (r) => r.page,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.pages(workspaceId) }),
  });
}

export function useUpdatePage(workspaceId: string, pageId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title?: string }) =>
      api<{ page: Page }>(pg(workspaceId, pageId), { method: "PATCH", json: body }).then(
        (r) => r.page,
      ),
    onSuccess: (page) => {
      qc.setQueryData(keys.page(workspaceId, pageId), page);
      qc.setQueryData<Page[]>(keys.pages(workspaceId), (pages) =>
        pages?.map((p) => (p.id === page.id ? { ...p, title: page.title } : p)),
      );
    },
  });
}

export function useDeletePage(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pageId: string) => api(pg(workspaceId, pageId), { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.pages(workspaceId) }),
  });
}

// --- comments -------------------------------------------------------------------------

export function useComments(workspaceId: string, pageId: string) {
  return useQuery({
    queryKey: keys.comments(workspaceId, pageId),
    queryFn: () =>
      api<{ comments: Comment[] }>(`${pg(workspaceId, pageId)}/comments`).then((r) => r.comments),
  });
}

export type NewComment = {
  body: string;
  thread_id?: string;
  anchor?: CommentAnchor;
  quote?: string;
};

export function useCommentMutations(workspaceId: string, pageId: string, onChange: () => void) {
  const qc = useQueryClient();
  const base = `${pg(workspaceId, pageId)}/comments`;
  const done = () => {
    onChange();
    return qc.invalidateQueries({ queryKey: keys.comments(workspaceId, pageId) });
  };
  return {
    create: useMutation({
      mutationFn: (body: NewComment) =>
        api<{ comment: Comment }>(base, { method: "POST", json: body }).then((r) => r.comment),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: string; body?: string; resolved?: boolean }) =>
        api(`${base}/${id}`, { method: "PATCH", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api(`${base}/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}

// --- versions -------------------------------------------------------------------------

export function useVersions(workspaceId: string, pageId: string, enabled = true) {
  return useQuery({
    queryKey: keys.versions(workspaceId, pageId),
    queryFn: () =>
      api<{ versions: Version[] }>(`${pg(workspaceId, pageId)}/versions`).then((r) => r.versions),
    enabled,
  });
}

export function fetchVersion(workspaceId: string, pageId: string, versionId: string) {
  return api<{ version: Version }>(`${pg(workspaceId, pageId)}/versions/${versionId}`).then(
    (r) => r.version,
  );
}

/** Snapshot the document. `state` is the editor's live Yjs state (base64). */
export function saveVersion(workspaceId: string, pageId: string, state: string, label?: string) {
  return api<{ version: Version }>(`${pg(workspaceId, pageId)}/versions`, {
    method: "POST",
    json: { label: label ?? null, state },
  }).then((r) => r.version);
}

// --- files ----------------------------------------------------------------------------

export function uploadFile(workspaceId: string, pageId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return api<{ file: StoredFile }>(`${pg(workspaceId, pageId)}/files`, {
    method: "POST",
    form,
  }).then((r) => r.file);
}

/** File metadata plus a presigned URL; refetched before the URL (5 min) runs out. */
export function useFile(workspaceId: string, pageId: string, fileId: string | null) {
  return useQuery({
    queryKey: keys.file(workspaceId, pageId, fileId ?? ""),
    queryFn: () =>
      api<{ file: StoredFile }>(`${pg(workspaceId, pageId)}/files/${fileId}`).then((r) => r.file),
    enabled: !!fileId,
    staleTime: 4 * 60 * 1000,
    refetchInterval: 4 * 60 * 1000,
  });
}
