/**
 * Stage F3: build the approved plan. Start (or continue) a build, follow its log while
 * the agent works task by task, stop it, and browse the files it wrote.
 */
import { useEffect, useRef, useState } from "react";

import { formatDateTime, timeAgo } from "@/lib/format";
import type { Page } from "@/lib/types";

import { BudgetLine } from "./Changes";
import {
  canBuild,
  plannedChanges,
  runActive,
  useFactoryFile,
  useFactoryFiles,
  useFactoryMutations,
  useRunLog,
  type Factory,
  type FactoryRun,
  type RunEvent,
} from "./api";
import { errorText, primary, secondary, Section } from "./ui";

const STATUS: Record<FactoryRun["status"], { label: string; tone: string }> = {
  queued: {
    label: "Waiting to start",
    tone: "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300",
  },
  running: {
    label: "Building",
    tone: "border-brand-500 text-brand-700 dark:text-brand-500",
  },
  stopping: {
    label: "Stopping after this step",
    tone: "border-amber-400 text-amber-700 dark:text-amber-300",
  },
  passed: {
    label: "Passed",
    tone: "border-emerald-500 text-emerald-700 dark:text-emerald-400",
  },
  failed: {
    label: "Failed",
    tone: "border-rose-400 text-rose-700 dark:text-rose-400",
  },
  stopped: {
    label: "Stopped",
    tone: "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300",
  },
};

const EVENT_ICON: Record<string, string> = {
  start: "▶",
  task: "📌",
  note: "💬",
  file: "📝",
  tests: "🧪",
  command: "⌨",
  done: "✅",
  blocked: "⛔",
  finish: "🏁",
};

function eventTone(e: RunEvent) {
  if (e.kind === "tests")
    return e.title.includes("passed") ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400";
  if (e.kind === "blocked") return "text-rose-700 dark:text-rose-400";
  if (e.kind === "task" || e.kind === "done") return "font-medium";
  if (e.kind === "note") return "text-zinc-500";
  return "";
}

function RunLog({ events, active }: { events: RunEvent[]; active: boolean }) {
  const box = useRef<HTMLOListElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [events.length]);
  return (
    <ol
      ref={box}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
      aria-label="Build log"
      aria-live="polite"
      className="max-h-96 overflow-y-auto rounded-lg border border-zinc-200 bg-zinc-50 p-2 font-mono text-xs dark:border-zinc-800 dark:bg-zinc-900"
    >
      {events.map((e) => (
        <li
          key={e.seq}
          className={`py-0.5 ${e.kind === "task" ? "mt-2 border-t border-zinc-200 pt-2 first:mt-0 first:border-0 first:pt-0.5 dark:border-zinc-800" : ""}`}
        >
          {e.detail &&
          (e.kind === "tests" ||
            e.kind === "command" ||
            e.kind === "blocked" ||
            e.kind === "finish" ||
            e.kind === "start") ? (
            <details>
              <summary className={`cursor-pointer ${eventTone(e)}`}>
                <span aria-hidden className="mr-1.5">
                  {EVENT_ICON[e.kind] ?? "•"}
                </span>
                {e.title}
              </summary>
              <pre className="mt-1 ml-5 whitespace-pre-wrap break-words text-zinc-600 dark:text-zinc-400">
                {e.detail}
              </pre>
            </details>
          ) : (
            <span className={eventTone(e)}>
              <span aria-hidden className="mr-1.5">
                {EVENT_ICON[e.kind] ?? "•"}
              </span>
              {e.title}
            </span>
          )}
        </li>
      ))}
      {active && <li className="py-0.5 text-zinc-400">…</li>}
      {!events.length && !active && <li className="py-0.5 text-zinc-400">No log yet.</li>}
    </ol>
  );
}

function Files({ page, version }: { page: Page; version: string }) {
  const files = useFactoryFiles(page.workspace_id, page.id, true, version);
  const [path, setPath] = useState<string | null>(null);
  const shown = path ?? files.data?.[0]?.path ?? null;
  const content = useFactoryFile(page.workspace_id, page.id, shown, version);
  if (!files.data?.length) return null;
  return (
    <div className="mt-4">
      <h3 className="mb-2 text-sm font-medium">
        Files <span className="font-normal text-zinc-500">({files.data.length})</span>
      </h3>
      <div className="grid overflow-hidden rounded-lg border border-zinc-200 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)] dark:border-zinc-800">
        <ul
          className="max-h-96 overflow-y-auto border-b border-zinc-200 py-1 text-xs sm:border-r sm:border-b-0 dark:border-zinc-800"
          aria-label="Files"
        >
          {files.data.map((f) => (
            <li key={f.path}>
              <button
                onClick={() => setPath(f.path)}
                aria-current={f.path === shown ? "true" : undefined}
                className={`block w-full truncate px-3 py-1 text-left font-mono ${
                  f.path === shown
                    ? "bg-brand-50 text-brand-700 dark:bg-zinc-800 dark:text-brand-500"
                    : "hover:bg-zinc-50 dark:hover:bg-zinc-900"
                }`}
                title={f.path}
              >
                {f.path}
              </button>
            </li>
          ))}
        </ul>
        <pre className="max-h-96 min-h-40 overflow-auto bg-white p-3 font-mono text-xs leading-relaxed dark:bg-zinc-950">
          {content.isLoading ? "Loading…" : (content.data ?? "")}
        </pre>
      </div>
    </div>
  );
}

function RunCard({ run }: { run: FactoryRun }) {
  const status = STATUS[run.status];
  const when = run.finished_at ?? run.started_at ?? run.created_at;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${status.tone}`}>{status.label}</span>
      <span>
        {run.tasks_done} of {run.tasks_total} tasks done
      </span>
      <span className="text-zinc-500">
        {run.steps} steps · {run.tokens.toLocaleString()} tokens
      </span>
      <span className="text-zinc-500" title={formatDateTime(when)}>
        {run.started_by?.display_name ?? "Someone"} · {timeAgo(when)}
      </span>
    </div>
  );
}

export function Build({ page, factory: f }: { page: Page; factory: Factory }) {
  const { startRun, stopRun } = useFactoryMutations(page.workspace_id, page.id);
  const latest = f.build.latest_run;
  const { run: live, events } = useRunLog(page.workspace_id, page.id, latest?.id ?? null);
  const run = live ?? latest;
  const active = runActive(run);
  const open = canBuild(f);
  const sandbox = f.build.sandbox;
  const remaining = f.tasks ? f.tasks.count - (run?.tasks_done ?? 0) : 0;
  const changes = plannedChanges(f);

  return (
    <Section
      n={5}
      title="Build"
      hint={
        open
          ? "An AI agent works through the tasks in order. For each one it writes the code and its tests, runs the tests in an isolated sandbox, and marks the task Done when they pass."
          : "Opens once the plan is approved."
      }
    >
      {open && (
        <>
          {sandbox === null && <p className="mb-3 text-sm text-zinc-500">Builds are turned off on this server.</p>}
          {sandbox === "fake" && (
            <p className="mb-3 text-xs text-amber-700 dark:text-amber-300">
              Demo sandbox: this server runs no code, so tests are only checked for syntax and counted. Set up Docker or
              a hosted Modal sandbox to run them for real.
            </p>
          )}

          {f.can_edit && sandbox && (
            <div className="mb-4 flex flex-wrap items-center gap-3">
              {active ? (
                <button
                  onClick={() => run && stopRun.mutate(run.id)}
                  disabled={stopRun.isPending}
                  className={secondary}
                >
                  {run?.status === "stopping" ? "Stopping… (press again to force)" : "Stop the build"}
                </button>
              ) : (
                <button
                  onClick={() => startRun.mutate()}
                  disabled={startRun.isPending}
                  className={run && !changes.length ? secondary : primary}
                >
                  {startRun.isPending
                    ? "Starting…"
                    : !run
                      ? "Start the build"
                      : changes.length
                        ? `Build ${changes.length === 1 ? "the change" : `${changes.length} changes`}`
                        : run.status === "passed"
                          ? "Build again"
                          : "Continue the build"}
                </button>
              )}
              {!active && changes.length > 0 && (
                <span className="text-xs text-zinc-500">
                  Only the new tasks for {changes.map((c) => `#${c.number}`).join(", ")} are built; Done tasks and their
                  files are kept.
                </span>
              )}
              {!active && !changes.length && run && run.status !== "passed" && remaining > 0 && (
                <span className="text-xs text-zinc-500">
                  Tasks already marked Done are kept; the build picks up from the next one.
                </span>
              )}
            </div>
          )}
          <BudgetLine page={page} factory={f} />
          {(startRun.isError || stopRun.isError) && (
            <p role="alert" className="mb-3 text-sm text-rose-600">
              {errorText(startRun.error ?? stopRun.error, "That did not work.")}
            </p>
          )}

          {run && (
            <>
              <RunCard run={run} />
              {run.status === "passed" && run.summary && (
                <p className="mb-3 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                  ✓ {run.summary} Review it below and approve it as a release.
                </p>
              )}
              {run.error && (
                <p
                  role="alert"
                  className="mb-3 rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
                >
                  {run.error}
                </p>
              )}
              <RunLog events={events} active={active} />
            </>
          )}
          <Files page={page} version={`${run?.id ?? "none"}-${run?.status ?? ""}-${f.build.file_count}`} />
        </>
      )}
    </Section>
  );
}
