/**
 * The app's checks (CI): lint, build, tests and a smoke run in the build sandbox after
 * every passed build. Shows the latest run step by step, the pass rate and the history
 * database, and lets editors run the checks again or let failures fix themselves.
 */
import { CircleCheck, CircleDashed, CircleX, LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";
import { Link } from "react-router";

import { Icon, type LucideIcon } from "@/components/icons";
import { timeAgo } from "@/lib/format";
import type { Page } from "@/lib/types";

import { checksActive, runActive, useFactoryMutations, type CiRun, type CiStatus, type Factory } from "./api";
import { errorText, quiet } from "./ui";

const RUN_STATUS: Record<CiStatus, { label: string; icon: LucideIcon; tone: string }> = {
  queued: { label: "waiting to start", icon: CircleDashed, tone: "text-zinc-500" },
  running: { label: "running", icon: LoaderCircle, tone: "text-brand-700 dark:text-brand-500" },
  passed: { label: "passed", icon: CircleCheck, tone: "text-emerald-700 dark:text-emerald-400" },
  failed: { label: "failed", icon: CircleX, tone: "text-rose-700 dark:text-rose-400" },
  error: { label: "could not run", icon: TriangleAlert, tone: "text-amber-700 dark:text-amber-300" },
};

const STEP_KEYS = ["lint", "build", "test", "smoke"] as const;
const STEP_NAMES: Record<string, string> = { lint: "Lint", build: "Build", test: "Tests", smoke: "Smoke run" };

/** The steps to show: the ones that ran, then the ones still to come while it runs. */
export function stepRows(run: CiRun) {
  const done = new Map(run.steps.map((s) => [s.key, s]));
  const active = run.status === "queued" || run.status === "running";
  return STEP_KEYS.filter((k) => done.has(k) || active).map((key, i, keys) => {
    const step = done.get(key);
    const firstPending = keys.findIndex((k) => !done.has(k));
    return {
      key,
      name: step?.name ?? STEP_NAMES[key],
      state: step ? step.status : run.status === "running" && i === firstPending ? "running" : "waiting",
      seconds: step?.seconds ?? null,
      command: step?.command ?? `sh .ymer-ci/${key}.sh`,
      output: step?.output ?? null,
    };
  });
}

/** "4 of 5 recent runs passed" style text for the pass rate. */
export function passRateText(f: Factory): string | null {
  const finished = f.checks.recent.filter((r) => r.status === "passed" || r.status === "failed");
  if (!finished.length || f.checks.pass_rate === null) return null;
  const passed = finished.filter((r) => r.status === "passed").length;
  return `${Math.round(f.checks.pass_rate * 100)}% pass rate (${passed} of ${finished.length} recent runs)`;
}

const STEP_ICON: Record<string, { icon: LucideIcon; tone: string }> = {
  passed: { icon: CircleCheck, tone: "text-emerald-600 dark:text-emerald-400" },
  failed: { icon: CircleX, tone: "text-rose-600 dark:text-rose-400" },
  running: { icon: LoaderCircle, tone: "animate-spin text-brand-600" },
  waiting: { icon: CircleDashed, tone: "text-zinc-400" },
};

export function Checks({ page, factory: f }: { page: Page; factory: Factory }) {
  const { runChecks, setAutoFix } = useFactoryMutations(page.workspace_id, page.id);
  const c = f.checks;
  const run = c.latest;
  const status = run ? RUN_STATUS[run.status] : null;
  const busy = checksActive(c) || runActive(f.build.latest_run);
  const rate = passRateText(f);

  return (
    <div className="mb-4 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800" aria-label="Checks">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <Icon icon={ShieldCheck} />
          Checks
        </h3>
        {run && status ? (
          <span className={`flex items-center gap-1 text-sm ${status.tone}`}>
            <Icon icon={status.icon} className={run.status === "running" ? "animate-spin" : ""} />
            Checks #{run.number} {status.label}
            <span className="text-zinc-500">
              · {run.trigger === "build" ? "after a build" : `run by ${run.started_by?.display_name ?? "someone"}`},{" "}
              {timeAgo(run.finished_at ?? run.created_at)}
            </span>
          </span>
        ) : (
          <span className="text-sm text-zinc-500">They run by themselves after every passed build.</span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {rate && <span className="text-xs text-zinc-500">{rate}</span>}
          {c.page_id && (
            <Link to={`/w/${page.workspace_id}/p/${c.page_id}`} className={quiet}>
              History
            </Link>
          )}
        </span>
      </div>

      {run && (
        <ul className="mt-2 space-y-1">
          {stepRows(run).map((s) => {
            const look = STEP_ICON[s.state] ?? STEP_ICON.waiting!;
            return (
              <li key={s.key} className="text-sm">
                <details open={s.state === "failed"} className="group">
                  <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
                    <Icon icon={look.icon} className={look.tone} />
                    <span className="font-medium">{s.name}</span>
                    <code className="truncate text-xs text-zinc-500">{s.command}</code>
                    {s.seconds !== null && <span className="ml-auto text-xs text-zinc-500">{s.seconds}s</span>}
                  </summary>
                  {s.output && (
                    <pre className="mt-1 max-h-56 overflow-auto rounded-md bg-zinc-50 p-2 font-mono text-xs whitespace-pre-wrap dark:bg-zinc-900">
                      {s.output}
                    </pre>
                  )}
                </details>
              </li>
            );
          })}
        </ul>
      )}

      {run?.summary && run.status !== "queued" && run.status !== "running" && (
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">{run.summary}</p>
      )}
      {run?.status === "failed" && run.change && (
        <p className="mt-1 text-sm">
          A fix is planned: change request #{run.change.number} added a task with the failing output to the Tasks
          database.{" "}
          {run.fix_run_id ? "Auto-fix is building it now." : "Build it in step 5 and the checks run again."}
        </p>
      )}

      {f.can_edit && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button onClick={() => runChecks.mutate()} disabled={busy || runChecks.isPending} className={quiet}>
            {checksActive(c) ? "Checking…" : "Run checks again"}
          </button>
          <label className="flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-300">
            <input
              type="checkbox"
              checked={c.auto_fix}
              onChange={(e) => setAutoFix.mutate(e.target.checked)}
              disabled={setAutoFix.isPending}
            />
            Fix failures automatically (up to {c.max_auto_fixes} builds in a row)
          </label>
          {c.auto_fix && c.fix_streak >= c.max_auto_fixes && (
            <span className="text-xs text-amber-700 dark:text-amber-300">
              Paused after {c.fix_streak} tries. Build the fix yourself, or turn it off and on to try again.
            </span>
          )}
        </div>
      )}
      {(runChecks.isError || setAutoFix.isError) && (
        <p role="alert" className="mt-2 text-sm text-rose-600">
          {errorText(runChecks.error ?? setAutoFix.error, "That did not work.")}
        </p>
      )}
    </div>
  );
}
