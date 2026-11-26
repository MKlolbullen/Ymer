import { AppearancePicker } from "@/components/AppearancePicker";
import { useState, type FormEvent, type ReactNode } from "react";
import { Navigate, Outlet, useNavigate, useOutletContext, useParams } from "react-router";

import { AppShell } from "@/components/AppShell";
import { useCreatePage, useCreateWorkspace, useWorkspaces } from "@/lib/queries";
import { atLeast, type Workspace } from "@/lib/types";
import { useBuiltinTemplates } from "@/templates/api";
import { TemplatesDialog, type TemplatesTab } from "@/templates/TemplatesDialog";

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
  const templates = useBuiltinTemplates();
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("getting-started");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const workspace = await create.mutateAsync({ name: name.trim(), template });
    navigate(`/w/${workspace.id}`);
  };
  const choices = [
    ...(templates.data?.workspace ?? []),
    { id: "blank", name: "Blank", description: "Start with an empty workspace.", icon: "⬜" },
  ];

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-8">
      <form onSubmit={submit} className="w-full max-w-lg space-y-4">
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
        <fieldset className="space-y-2">
          <legend className="mb-2 text-sm font-medium">Start with</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {choices.map((t) => (
              <label
                key={t.id}
                className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-left ${
                  template === t.id ? "border-brand-500 bg-brand-50/50 dark:bg-zinc-800" : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-800"
                }`}
              >
                <input type="radio" name="template" value={t.id} checked={template === t.id} onChange={() => setTemplate(t.id)} className="sr-only" />
                <span aria-hidden className="text-xl leading-none">
                  {t.icon}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{t.name}</span>
                  <span className="block text-xs text-zinc-500">{t.description}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
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
  const [library, setLibrary] = useState<TemplatesTab | null>(null);

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">{workspace.name}</h1>
      <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
        Pick a page in the sidebar{canCreate ? ", or start something new." : "."} Press Ctrl K (⌘K on a Mac) to search.
      </p>
      {canCreate && (
        <div className="mt-6 flex flex-wrap gap-2">
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
          <button
            onClick={() => setLibrary("templates")}
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
          >
            From a template
          </button>
          <button
            onClick={() => setLibrary("import")}
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
          >
            Import
          </button>
        </div>
      )}
      {library && <TemplatesDialog workspaceId={workspace.id} parentId={null} initialTab={library} onClose={() => setLibrary(null)} />}
    </div>
  );
}
