import type { ReactNode } from "react";

import { ApiStatus } from "./ApiStatus";

const NAV = ["Home", "Documents", "Databases", "Mind maps", "Files"] as const;

/**
 * Top-level layout: sidebar (workspace + navigation), header and main content.
 * Navigation items are placeholders until routing and pages land.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-zinc-200 bg-zinc-50 md:flex dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex h-14 items-center gap-2 px-4">
          <img src="/favicon.svg" alt="" className="size-6" />
          <span className="font-semibold tracking-tight">Ymer</span>
        </div>
        <nav aria-label="Main" className="flex-1 space-y-0.5 px-2 py-2">
          {NAV.map((item, i) => (
            <a
              key={item}
              href="#"
              aria-current={i === 0 ? "page" : undefined}
              className="block rounded-md px-3 py-1.5 text-sm text-zinc-600 hover:bg-zinc-200/60 aria-[current=page]:bg-zinc-200/80 aria-[current=page]:font-medium aria-[current=page]:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:aria-[current=page]:bg-zinc-800 dark:aria-[current=page]:text-zinc-100"
            >
              {item}
            </a>
          ))}
        </nav>
        <div className="border-t border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <ApiStatus />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-zinc-200 px-4 sm:px-6 dark:border-zinc-800">
          <span className="font-semibold md:hidden">Ymer</span>
          <span className="hidden text-sm text-zinc-500 md:inline">Home</span>
          <div className="md:hidden">
            <ApiStatus />
          </div>
        </header>
        <main className="flex-1 overflow-y-auto px-4 py-8 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
