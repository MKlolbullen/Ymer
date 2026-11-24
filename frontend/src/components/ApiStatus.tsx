import { useEffect, useState } from "react";

import { apiGet, type HealthResponse } from "@/lib/api";

type State = { kind: "loading" } | { kind: "ok"; version: string } | { kind: "down" };

/** Small indicator showing whether the backend's /api/health answers. */
export function ApiStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    apiGet<HealthResponse>("/health", { signal: controller.signal })
      .then((h) => setState({ kind: "ok", version: h.version }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ kind: "down" });
      });
    return () => controller.abort();
  }, []);

  const dot =
    state.kind === "ok" ? "bg-emerald-500" : state.kind === "down" ? "bg-rose-500" : "bg-zinc-400";
  const label =
    state.kind === "ok"
      ? `API ${state.version}`
      : state.kind === "down"
        ? "API unreachable"
        : "Checking API…";

  return (
    <span
      className="inline-flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400"
      role="status"
    >
      <span className={`size-2 rounded-full ${dot}`} aria-hidden />
      {label}
    </span>
  );
}
