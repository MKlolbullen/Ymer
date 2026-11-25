import { AppearancePicker } from "@/components/AppearancePicker";
import { useState, type FormEvent, type ReactNode } from "react";
import { Navigate, Outlet, useNavigate, useOutletContext, useParams } from "react-router";

import { AppShell } from "@/components/AppShell";
import { useCreatePage, useCreateWorkspace, useWorkspaces } from "@/lib/queries";
import { atLeast, type Workspace } from "@/lib/types";

type LayoutContext = { workspace: Workspace; setHeader: (node: ReactNode) => void };

export function useWorkspaceLayout(): LayoutContext {
  return useOutletContext<LayoutContext>();
}

const Centered = ({ children }: { children: ReactNode }) => (
  <div className="flex h-full items-center justify-center p-6 text-sm text-zinc-500">{children}</div>
);

/** "/": open the first workspace, or ask for one when there is none. */
export function Home() {
  const workspaces = useWorkspaces();
  if (workspaces.isLoading) return <Centered>Loading…</Centered>;
  if (workspaces.isError) return <Centered>Could not load your workspaces.</Centered>;
  const first = workspaces.data?.[0];
  return first ? <Navigate to={`/w/${first.id}`} replace /> : <Navigate to="/new-workspace" replace />;
}

export function NewWorkspace() {
  const navigate = useNavigate();
  const create = useCreateWorkspace();
  const workspaces = useWorkspaces();
  const [name, setName] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const workspace = await create.mutateAsync(name.trim());
    navigate(`/w/${workspace.id}`);
  };

  return (
    <div className="flex min-h-full items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <h1 className="text-lg font-semibold">Create a workspace</h1>
        <p className="text-sm text-zinc-500">A workspace holds your pages and the people you work with.</p>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Research notes"
          maxLength={120}
          required
          className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        />
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={create.isPending || !name.trim()}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-on-brand hover:bg-brand-700 disabled:opacity-60"
          >
            Create workspace
          </button>
          {!!workspaces.data?.length && (
            <button type="button" onClick={() => navigate(-1)} className="px-3 text-sm text-zinc-500">
              Cancel
            </button>
          )}
        </div>
        <AppearancePicker />
      </form>
    </div>
  );
}

export function WorkspaceLayout() {
  const { workspaceId } = useParams();
  const workspaces = useWorkspaces();
  const [header, setHeader] = useState<ReactNode>(null);

  if (workspaces.isLoading) return <Centered>Loading…</Centered>;
  const workspace = workspaces.data?.find((w) => w.id === workspaceId);
  if (!workspace) return <Centered>This workspace does not exist or you do not have access to it.</Centered>;

  return (
    <AppShell workspace={workspace} workspaces={workspaces.data!} header={header}>
      <Outlet context={{ workspace, setHeader } satisfies LayoutContext} />
    </AppShell>
  );
}

export function WorkspaceHome() {
  const { workspace } = useWorkspaceLayout();
  const navigate = useNavigate();
  const create = useCreatePage(workspace.id);
  const canCreate = atLeast(workspace.role, "editor");

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">{workspace.name}</h1>
      <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
        Pick a page in the sidebar{canCreate ? ", or start something new." : "."} Press Ctrl K (⌘K on a Mac) to search.
      </p>
      {canCreate && (
        <div className="mt-6 flex gap-2">
          {(["document", "database"] as const).map((type) => (
            <button
              key={type}
              onClick={async () => {
                const page = await create.mutateAsync({ type });
                navigate(`/w/${workspace.id}/p/${page.id}`);
              }}
              className={
                type === "document"
                  ? "rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-on-brand hover:bg-brand-700"
                  : "rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
              }
            >
              New {type}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
