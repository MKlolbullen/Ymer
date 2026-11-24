import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { logout, useSession } from "@/lib/auth";
import { useCreatePage, usePages } from "@/lib/queries";
import { atLeast, type Workspace } from "@/lib/types";

import { ApiStatus } from "./ApiStatus";
import { PageTree } from "./PageTree";

type Props = {
  workspace: Workspace;
  workspaces: Workspace[];
  header?: ReactNode;
  children: ReactNode;
};

/** Layout: sidebar (workspace switcher, page tree, account) plus header and content. */
export function AppShell({ workspace, workspaces, header, children }: Props) {
  const navigate = useNavigate();
  const session = useSession();
  const pages = usePages(workspace.id);
  const create = useCreatePage(workspace.id);
  const [menuOpen, setMenuOpen] = useState(false);

  const newPage = async () => {
    const page = await create.mutateAsync({ type: "document" });
    setMenuOpen(false);
    navigate(`/w/${workspace.id}/p/${page.id}`);
  };

  const sidebar = (
    <>
      <div className="flex h-14 items-center gap-2 px-3">
        <img src="/favicon.svg" alt="" className="size-6" />
        <select
          aria-label="Workspace"
          value={workspace.id}
          onChange={(e) => {
            if (e.target.value === "__new") navigate("/new-workspace");
            else navigate(`/w/${e.target.value}`);
          }}
          className="min-w-0 flex-1 truncate rounded-md bg-transparent py-1 text-sm font-semibold hover:bg-zinc-200/60 focus:outline-none dark:hover:bg-zinc-800"
        >
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
          <option value="__new">+ New workspace…</option>
        </select>
      </div>
      <div className="flex items-center justify-between px-3 pt-2 pb-1">
        <span className="text-xs font-medium tracking-wide text-zinc-500 uppercase">Pages</span>
        {atLeast(workspace.role, "editor") && (
          <button
            onClick={newPage}
            disabled={create.isPending}
            className="rounded px-1.5 text-sm text-zinc-500 hover:bg-zinc-200/60 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-white"
            title="New page"
            aria-label="New page"
          >
            +
          </button>
        )}
      </div>
      <nav aria-label="Pages" className="flex-1 overflow-y-auto px-1 pb-2" onClick={() => setMenuOpen(false)}>
        {pages.data ? <PageTree pages={pages.data} workspaceId={workspace.id} /> : <p className="px-3 text-xs text-zinc-500">Loading…</p>}
      </nav>
      <div className="space-y-2 border-t border-zinc-200 px-3 py-3 dark:border-zinc-800">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm" title={session?.user.email}>
            {session?.user.display_name}
          </span>
          <button onClick={() => void logout()} className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-white">
            Sign out
          </button>
        </div>
        <ApiStatus />
      </div>
    </>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-zinc-200 bg-zinc-50 md:flex dark:border-zinc-800 dark:bg-zinc-900">
        {sidebar}
      </aside>
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMenuOpen(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <aside
            className="absolute inset-y-0 left-0 flex w-72 max-w-[85%] flex-col bg-zinc-50 dark:bg-zinc-900"
            onClick={(e) => e.stopPropagation()}
          >
            {sidebar}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-zinc-200 px-4 sm:px-6 dark:border-zinc-800">
          <button
            className="-ml-1 rounded p-1 text-zinc-600 md:hidden"
            aria-label="Open menu"
            onClick={() => setMenuOpen(true)}
          >
            ☰
          </button>
          <div className="flex min-w-0 flex-1 items-center gap-3">{header}</div>
        </header>
        <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
