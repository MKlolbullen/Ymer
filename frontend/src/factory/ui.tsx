/** Shared pieces of the Factory page. */
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";

export const primary =
  "rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50";
export const secondary =
  "rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900";
export const quiet = "rounded px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800";

export function errorText(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

export function Section({ n, title, hint, children }: { n: number; title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-10" aria-labelledby={`factory-step-${n}`}>
      <h2 id={`factory-step-${n}`} className="flex items-center gap-2 text-lg font-semibold tracking-tight">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-100 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {n}
        </span>
        {title}
      </h2>
      {hint && <p className="mt-1 mb-3 text-sm text-zinc-500">{hint}</p>}
      {children}
    </section>
  );
}
