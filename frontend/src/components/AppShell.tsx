import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router";

import { useAiStatus } from "@/ai/api";
import { logout, useSession } from "@/lib/auth";
import { usePages } from "@/lib/queries";
import { useOnline } from "@/offline/connectivity";
import { usePendingPages } from "@/offline/store";
import { useBackgroundSync } from "@/offline/sync";
import { atLeast, type Workspace } from "@/lib/types";

import { AppearancePicker } from "./AppearancePicker";
import { ApiStatus } from "./ApiStatus";
import { NewPageButton, PageTree } from "./PageTree";
import { SearchDialog } from "./SearchDialog";

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
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const ai = useAiStatus();
  const online = useOnline();
  const pending = usePendingPages(session?.user.id);
  useBackgroundSync(session?.user.id);

  const signOut = () => {
    if (
      pending.length &&
      !window.confirm(
        `${pending.length === 1 ? "1 page has" : `${pending.length} pages have`} offline edits that have not synced yet. Signing out deletes them. Sign out anyway?`,
      )
    )
      return;
    void logout();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `flex w-full items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-zinc-200/60 dark:hover:bg-zinc-800 ${
      isActive ? "bg-brand-50 font-semibold text-brand-700 dark:bg-zinc-800 dark:text-brand-500" : "text-zinc-600 dark:text-zinc-400"
    }`;

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
      <div className="space-y-0.5 px-1">
        <button
          onClick={() => {
            setMenuOpen(false);
            setSearchOpen(true);
          }}
          className={navClass({ isActive: false })}
        >
          <span aria-hidden>🔍</span> Search
          <kbd className="ml-auto text-xs text-zinc-400 pointer-coarse:hidden">Ctrl K</kbd>
        </button>
        {ai.data?.enabled && (
          <NavLink to={`/w/${workspace.id}/ask`} className={navClass} onClick={() => setMenuOpen(false)}>
            <span aria-hidden>✨</span> Ask AI
          </NavLink>
        )}
        {workspace.role !== null && (
          <NavLink to={`/w/${workspace.id}/members`} className={navClass} onClick={() => setMenuOpen(false)}>
            <span aria-hidden>👥</span> Members
          </NavLink>
        )}
        {workspace.role === "owner" && (
          <NavLink to={`/w/${workspace.id}/activity`} className={navClass} onClick={() => setMenuOpen(false)}>
            <span aria-hidden>🛡</span> Activity log
          </NavLink>
        )}
      </div>
      <div className="flex items-center justify-between px-3 pt-3 pb-1">
        <span className="text-xs font-medium tracking-wide text-zinc-500 uppercase">Pages</span>
        {atLeast(workspace.role, "editor") && (
          <NewPageButton
            workspaceId={workspace.id}
            parentId={null}
            label="New page"
            onCreated={() => setMenuOpen(false)}
            className="rounded px-1.5 text-sm text-zinc-500 hover:bg-zinc-200/60 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-white"
          />
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
          <button
            onClick={signOut}
            disabled={!online}
            title={online ? undefined : "Reconnect to sign out"}
            className="text-xs text-zinc-500 hover:text-zinc-900 disabled:opacity-50 dark:hover:text-white"
          >
            Sign out
          </button>
        </div>
        <NavLink to={`/w/${workspace.id}/settings`} className={navClass} onClick={() => setMenuOpen(false)}>
          <span aria-hidden>⚙</span> AI settings
        </NavLink>
        <AppearancePicker />
        {pending.length > 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-300" role="status">
            {pending.length === 1 ? "1 page" : `${pending.length} pages`} with offline edits, waiting to sync
          </p>
        )}
        <ApiStatus />
      </div>
    </>
  );

  return (
    <div className="flex h-full">
      <aside className="workspace-sidebar hidden w-64 shrink-0 flex-col border-r border-zinc-200 bg-zinc-50 md:flex dark:border-zinc-800 dark:bg-zinc-900">
        {sidebar}
      </aside>
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMenuOpen(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <aside
            className="workspace-sidebar absolute inset-y-0 left-0 flex w-72 max-w-[85%] flex-col bg-zinc-50 dark:bg-zinc-900"
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
          {!online && (
            <span
              role="status"
              title="Pages you have opened before still work. Edits sync when you reconnect."
              className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-200"
            >
              Offline
            </span>
          )}
        </header>
        <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">{children}</main>
      </div>
      {searchOpen && <SearchDialog workspaceId={workspace.id} onClose={() => setSearchOpen(false)} />}
    </div>
  );
}
