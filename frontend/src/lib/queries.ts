/** Server state (React Query). Keys start with the workspace id so a switch is clean. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "./api";
import type {
  ChartConfig,
  ChartData,
  Comment,
  CommentAnchor,
  DatabaseView,
  Grant,
  Member,
  Page,
  PageType,
  Property,
  PropertyConfig,
  PropertyType,
  Role,
  Row,
  RowValues,
  SearchResult,
  StoredFile,
  Version,
  ViewConfig,
  ViewType,
  Workspace,
} from "./types";

const ws = (id: string) => `/workspaces/${id}`;
const pg = (workspaceId: string, pageId: string) => `${ws(workspaceId)}/pages/${pageId}`;

export const keys = {
  workspaces: ["workspaces"] as const,
  pages: (w: string) => ["pages", w] as const,
  page: (w: string, p: string) => ["pages", w, p] as const,
  comments: (w: string, p: string) => ["comments", w, p] as const,
  versions: (w: string, p: string) => ["versions", w, p] as const,
  file: (w: string, p: string, f: string) => ["file", w, p, f] as const,
  members: (w: string) => ["members", w] as const,
  grants: (w: string, p: string) => ["grants", w, p] as const,
  backlinks: (w: string, p: string) => ["backlinks", w, p] as const,
  search: (w: string, q: string) => ["search", w, q] as const,
  database: (w: string, d: string) => ["database", w, d] as const,
  rows: (w: string, d: string) => ["rows", w, d] as const,
  chart: (w: string, d: string) => ["rows", w, d, "chart"] as const,
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
    mutationFn: ({ name, template = "blank" }: { name: string; template?: string }) =>
      api<{ workspace: Workspace }>("/workspaces", { method: "POST", json: { name, template } }).then(
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
    mutationFn: (body: { type: PageType; title?: string; parent_id?: string | null; position?: number }) =>
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

/** Drag and drop in the tree: new parent, placed before `before_id` (or at the end). */
export function useMovePage(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, ...body }: { pageId: string; parent_id: string | null; before_id: string | null }) =>
      api<{ page: Page }>(`${pg(workspaceId, pageId)}/move`, { method: "POST", json: body }).then((r) => r.page),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.pages(workspaceId) }),
  });
}

export function useBacklinks(workspaceId: string, pageId: string) {
  return useQuery({
    queryKey: keys.backlinks(workspaceId, pageId),
    queryFn: () => api<{ pages: Page[] }>(`${pg(workspaceId, pageId)}/backlinks`).then((r) => r.pages),
  });
}

export function useSearch(workspaceId: string, q: string) {
  return useQuery({
    queryKey: keys.search(workspaceId, q),
    queryFn: ({ signal }) =>
      api<{ results: SearchResult[] }>(`${ws(workspaceId)}/search?q=${encodeURIComponent(q)}`, { signal }).then(
        (r) => r.results,
      ),
    enabled: q.trim().length > 0,
    staleTime: 5_000,
    placeholderData: (previous) => previous,
  });
}

// --- members and sharing --------------------------------------------------------------

export function useMembers(workspaceId: string, enabled = true) {
  return useQuery({
    queryKey: keys.members(workspaceId),
    queryFn: () => api<{ members: Member[] }>(`${ws(workspaceId)}/members`).then((r) => r.members),
    enabled,
  });
}

export function useMemberMutations(workspaceId: string) {
  const qc = useQueryClient();
  const base = `${ws(workspaceId)}/members`;
  const done = () => qc.invalidateQueries({ queryKey: keys.members(workspaceId) });
  return {
    add: useMutation({
      mutationFn: (body: { email: string; role: Role }) => api(base, { method: "POST", json: body }),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
        api(`${base}/${userId}`, { method: "PATCH", json: { role } }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (userId: string) => api(`${base}/${userId}`, { method: "DELETE" }),
      onSuccess: () => {
        void done();
        return qc.invalidateQueries({ queryKey: keys.workspaces });
      },
    }),
  };
}

export function useGrants(workspaceId: string, pageId: string, enabled = true) {
  return useQuery({
    queryKey: keys.grants(workspaceId, pageId),
    queryFn: () => api<{ permissions: Grant[] }>(`${pg(workspaceId, pageId)}/permissions`).then((r) => r.permissions),
    enabled,
  });
}

export function useGrantMutations(workspaceId: string, pageId: string) {
  const qc = useQueryClient();
  const base = `${pg(workspaceId, pageId)}/permissions`;
  const done = () => qc.invalidateQueries({ queryKey: keys.grants(workspaceId, pageId) });
  return {
    share: useMutation({
      mutationFn: (body: { email: string; role: Role }) => api(base, { method: "POST", json: body }),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
        api(`${base}/${userId}`, { method: "PUT", json: { role } }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (userId: string) => api(`${base}/${userId}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}

// --- databases ------------------------------------------------------------------------

const dbPath = (workspaceId: string, databaseId: string) => `${ws(workspaceId)}/databases/${databaseId}`;

export function useDatabase(workspaceId: string, databaseId: string) {
  return useQuery({
    queryKey: keys.database(workspaceId, databaseId),
    queryFn: () => api<{ properties: Property[]; views: DatabaseView[] }>(dbPath(workspaceId, databaseId)),
    enabled: !!databaseId,
  });
}

/** Rows as a view shows them (server-side filters and sorts). Polls so edits by others show up. */
export function useRows(workspaceId: string, databaseId: string, viewId: string | null, q = "") {
  const params = new URLSearchParams();
  if (viewId) params.set("view_id", viewId);
  if (q) params.set("q", q);
  return useQuery({
    queryKey: [...keys.rows(workspaceId, databaseId), viewId, q],
    queryFn: () => api<{ rows: Row[] }>(`${dbPath(workspaceId, databaseId)}/rows?${params}`).then((r) => r.rows),
    refetchInterval: 15_000,
    placeholderData: (previous) => previous,
  });
}

/**
 * Grouped and aggregated rows for a chart. Keyed under the database's rows, so any row
 * change (which invalidates ["rows", workspace]) redraws charts too. Polls like rows do.
 */
export function useChartData(workspaceId: string, databaseId: string | null, config: ChartConfig, viewId: string | null = null) {
  return useQuery({
    queryKey: [...keys.chart(workspaceId, databaseId ?? ""), viewId, config],
    queryFn: () =>
      api<{ chart: ChartData }>(`${dbPath(workspaceId, databaseId!)}/chart`, {
        method: "POST",
        json: { config, view_id: viewId },
      }).then((r) => r.chart),
    enabled: !!databaseId,
    refetchInterval: 15_000,
    placeholderData: (previous) => previous,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  });
}

export function useDatabaseMutations(workspaceId: string, databaseId: string) {
  const qc = useQueryClient();
  const base = dbPath(workspaceId, databaseId);
  // Relations and rollups mean a change here can show up in other databases too.
  const refreshRows = () => qc.invalidateQueries({ queryKey: ["rows", workspaceId] });
  const refreshSchema = () =>
    Promise.all([qc.invalidateQueries({ queryKey: ["database", workspaceId] }), refreshRows()]);
  return {
    createRow: useMutation({
      mutationFn: (body: { values?: RowValues; position?: number }) =>
        api<{ row: Row }>(`${base}/rows`, { method: "POST", json: body }).then((r) => r.row),
      onSuccess: refreshRows,
    }),
    updateRow: useMutation({
      mutationFn: ({ rowId, ...body }: { rowId: string; values?: RowValues; position?: number }) =>
        api<{ row: Row }>(`${base}/rows/${rowId}`, { method: "PATCH", json: body }).then((r) => r.row),
      onSuccess: refreshRows,
    }),
    deleteRow: useMutation({
      mutationFn: (rowId: string) => api(`${base}/rows/${rowId}`, { method: "DELETE" }),
      onSuccess: refreshRows,
    }),
    createProperty: useMutation({
      mutationFn: (body: {
        name: string;
        type: PropertyType;
        config?: PropertyConfig;
        two_way?: boolean;
        reverse_name?: string;
      }) => api<{ property: Property }>(`${base}/properties`, { method: "POST", json: body }).then((r) => r.property),
      onSuccess: refreshSchema,
    }),
    updateProperty: useMutation({
      mutationFn: ({
        propertyId,
        ...body
      }: {
        propertyId: string;
        name?: string;
        type?: PropertyType;
        config?: PropertyConfig;
        position?: number;
      }) =>
        api<{ property: Property }>(`${base}/properties/${propertyId}`, { method: "PATCH", json: body }).then(
          (r) => r.property,
        ),
      onSuccess: refreshSchema,
    }),
    deleteProperty: useMutation({
      mutationFn: (propertyId: string) => api(`${base}/properties/${propertyId}`, { method: "DELETE" }),
      onSuccess: refreshSchema,
    }),
    createView: useMutation({
      mutationFn: (body: { name: string; type: ViewType; config?: Partial<ViewConfig> }) =>
        api<{ view: DatabaseView }>(`${base}/views`, { method: "POST", json: body }).then((r) => r.view),
      onSuccess: refreshSchema,
    }),
    updateView: useMutation({
      mutationFn: ({
        viewId,
        ...body
      }: {
        viewId: string;
        name?: string;
        type?: ViewType;
        config?: ViewConfig;
      }) => api<{ view: DatabaseView }>(`${base}/views/${viewId}`, { method: "PATCH", json: body }).then((r) => r.view),
      onSuccess: refreshSchema,
    }),
    deleteView: useMutation({
      mutationFn: (viewId: string) => api(`${base}/views/${viewId}`, { method: "DELETE" }),
      onSuccess: refreshSchema,
    }),
  };
}

/** Rows of another database, for the relation picker. */
export function fetchRows(workspaceId: string, databaseId: string, q: string) {
  return api<{ rows: Row[] }>(`${dbPath(workspaceId, databaseId)}/rows?q=${encodeURIComponent(q)}`).then(
    (r) => r.rows,
  );
}

export function fetchDatabase(workspaceId: string, databaseId: string) {
  return api<{ properties: Property[]; views: DatabaseView[] }>(dbPath(workspaceId, databaseId));
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
