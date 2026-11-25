/** Templates, imports and duplicating pages (backend: ymer/api/templates.py). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { keys } from "@/lib/queries";
import type { Page } from "@/lib/types";

export type Template = { id: string; name: string; description: string; icon: string; kind: "page" | "workspace" };

export type Catalog = { page: Template[]; workspace: Template[]; custom?: Page[] };

export type ImportSummary = {
  pages: number;
  databases?: number;
  rows?: number;
  files: number;
  warnings: string[];
};

const ws = (id: string) => `/workspaces/${id}`;

/** Built-in templates, for picking a starter set when creating a workspace. */
export function useBuiltinTemplates() {
  return useQuery({ queryKey: ["templates"], queryFn: () => api<Catalog>("/templates"), staleTime: Infinity });
}

/** Built-in templates plus the workspace's own (pages saved as templates). */
export function useTemplates(workspaceId: string) {
  return useQuery({
    queryKey: ["templates", workspaceId],
    queryFn: () => api<Catalog>(`${ws(workspaceId)}/templates`),
  });
}

type Created = { page: Page | null; pages: Page[]; summary: ImportSummary };

export function useApplyTemplate(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { template_id?: string; page_id?: string; parent_id: string | null }) =>
      api<Created>(`${ws(workspaceId)}/templates/apply`, { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.pages(workspaceId) }),
  });
}

export function useImport(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ files, parentId }: { files: File[]; parentId: string | null }) => {
      const form = new FormData();
      for (const file of files) form.append("file", file, file.name);
      if (parentId) form.append("parent_id", parentId);
      return api<{ pages: Page[]; summary: ImportSummary }>(`${ws(workspaceId)}/import`, { method: "POST", form });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.pages(workspaceId) }),
  });
}

export function useDuplicatePage(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pageId: string) =>
      api<{ page: Page; summary: ImportSummary }>(`${ws(workspaceId)}/pages/${pageId}/duplicate`, {
        method: "POST",
        json: {},
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.pages(workspaceId) }),
  });
}

/** Save a page as a template for the workspace, or stop offering it as one. */
export function useSetTemplate(workspaceId: string, pageId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (isTemplate: boolean) =>
      api<{ page: Page }>(`${ws(workspaceId)}/pages/${pageId}`, {
        method: "PATCH",
        json: { is_template: isTemplate },
      }).then((r) => r.page),
    onSuccess: (page) => {
      qc.setQueryData(keys.page(workspaceId, pageId), page);
      void qc.invalidateQueries({ queryKey: ["templates", workspaceId] });
    },
  });
}

/** What an import or template use made, in one sentence. */
export function describeSummary(summary: ImportSummary): string {
  const parts = [`${summary.pages} page${summary.pages === 1 ? "" : "s"}`];
  if (summary.databases) parts.push(`${summary.databases} database${summary.databases === 1 ? "" : "s"} with ${summary.rows ?? 0} rows`);
  if (summary.files) parts.push(`${summary.files} file${summary.files === 1 ? "" : "s"}`);
  return `Added ${parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0]}.`;
}

/** Files the import accepts (zips are unpacked on the server). */
export function importable(file: File): boolean {
  return /\.(zip|md|markdown|csv)$/i.test(file.name);
}
