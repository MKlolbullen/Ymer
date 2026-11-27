/**
 * Stage F5: iterate on a built app. People request changes (or the factory finds them in
 * edits to the source pages), planning turns them into new tasks, and the next build
 * works only on those. Also the workspace's monthly token budget for builds.
 */
import { CircleCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { Icon } from "@/components/icons";
import { formatDateTime, timeAgo } from "@/lib/format";
import type { Page } from "@/lib/types";

import {
  budgetShare,
  canIterate,
  openChanges,
  plannedChanges,
  runActive,
  tokens,
  useFactoryMutations,
  type ChangeRequest,
  type ChangeStatus,
  type Factory,
} from "./api";
import { errorText, primary, quiet, secondary, Section } from "./ui";

const input =
  "w-full rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900";

const STATUS: Record<ChangeStatus, { label: string; tone: string }> = {
  open: { label: "Open", tone: "border-brand-500 text-brand-700 dark:text-brand-500" },
  planned: { label: "Planned", tone: "border-amber-400 text-amber-700 dark:text-amber-300" },
  built: { label: "Built", tone: "border-emerald-500 text-emerald-700 dark:text-emerald-400" },
  released: { label: "Released", tone: "border-emerald-500 text-emerald-700 dark:text-emerald-400" },
  dismissed: { label: "Dismissed", tone: "border-zinc-300 text-zinc-500 dark:border-zinc-700" },
};

function statusText(c: ChangeRequest) {
  if (c.status === "released" && c.release) return `In release ${c.release}`;
  return STATUS[c.status].label;
}

function ChangeItem({ page, change: c, editable }: { page: Page; change: ChangeRequest; editable: boolean }) {
  const { updateChange } = useFactoryMutations(page.workspace_id, page.id);
  const tone = STATUS[c.status].tone;
  return (
    <li
      className={`rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800 ${c.status === "dismissed" ? "opacity-60" : ""}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-xs text-zinc-500">#{c.number}</span>
        <span className="font-medium">{c.title}</span>
        <span className={`rounded-full border px-2 py-0.5 text-xs ${tone}`}>{statusText(c)}</span>
        <span className="text-xs text-zinc-500" title={formatDateTime(c.created_at)}>
          {c.origin === "spec"
            ? "From edits to the spec"
            : c.origin === "ci"
              ? "From failing checks"
              : `Asked by ${c.created_by?.display_name ?? "someone"}`},{" "}
          {timeAgo(c.created_at)}
        </span>
        {editable && (c.status === "open" || c.status === "dismissed") && (
          <button
            onClick={() => updateChange.mutate({ id: c.id, status: c.status === "open" ? "dismissed" : "open" })}
            disabled={updateChange.isPending}
            className={`${quiet} ml-auto`}
          >
            {c.status === "open" ? "Dismiss" : "Reopen"}
          </button>
        )}
      </div>
      {c.detail && <p className="mt-1 whitespace-pre-wrap text-zinc-600 dark:text-zinc-300">{c.detail}</p>}
      {c.tasks.length > 0 && (
        <p className="mt-1 text-xs text-zinc-500">
          {c.tasks.length === 1 ? "Task" : "Tasks"}: {c.tasks.join(" · ")}
        </p>
      )}
    </li>
  );
}

function NewChange({ page }: { page: Page }) {
  const { addChange } = useFactoryMutations(page.workspace_id, page.id);
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        addChange.mutate(
          { title, detail },
          {
            onSuccess: () => {
              setTitle("");
              setDetail("");
            },
          },
        );
      }}
      className="space-y-2"
    >
      <label className="block text-sm">
        <span className="mb-1 block font-medium">Request a change</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
          placeholder="What should be different, like “Add a dark mode”"
          className={input}
        />
      </label>
      <textarea
        value={detail}
        onChange={(e) => setDetail(e.target.value)}
        rows={2}
        maxLength={4000}
        placeholder="Details (optional): what it should do, where, and how you will know it works"
        aria-label="Change details"
        className={input}
      />
      <button type="submit" disabled={addChange.isPending || !title.trim()} className={secondary}>
        {addChange.isPending ? "Adding…" : "Add request"}
      </button>
      {addChange.isError && (
        <p role="alert" className="text-sm text-rose-600">
          {errorText(addChange.error, "The request could not be added.")}
        </p>
      )}
    </form>
  );
}

export function Iterate({ page, factory: f }: { page: Page; factory: Factory }) {
  const { changesFromSpec, planChanges } = useFactoryMutations(page.workspace_id, page.id);
  const open = canIterate(f);
  const pending = openChanges(f);
  const building = runActive(f.build.latest_run);
  const [showAll, setShowAll] = useState(false);
  const closed = f.changes.filter((c) => c.status === "released" || c.status === "dismissed");
  const shown = showAll ? f.changes : f.changes.filter((c) => !closed.includes(c));
  const fromSpec = changesFromSpec.data;
  // Shown until the planned tasks are built.
  const planned = plannedChanges(f).length > 0 ? planChanges.data : undefined;

  return (
    <Section
      n={8}
      title="Change"
      hint={
        open
          ? "Ask for changes, or edit the source pages. Planning adds tasks for the changes; the next build works only on those, starting from the current files, and the result is reviewed as the next release."
          : "Opens once the plan is approved."
      }
    >
      {open && (
        <div className="space-y-4">
          {f.spec_changes.length > 0 && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950">
              <p className="text-amber-900 dark:text-amber-200">
                {f.spec_changes.length === 1 ? "A source page was" : `${f.spec_changes.length} source pages were`}{" "}
                edited since the factory read {f.spec_changes.length === 1 ? "it" : "them"}:{" "}
                {f.spec_changes.map((s, i) => (
                  <span key={s.page_id}>
                    {i > 0 && ", "}
                    <Link to={`/w/${page.workspace_id}/p/${s.page_id}`} className="font-medium underline">
                      {s.title || "Untitled"}
                    </Link>
                  </span>
                ))}
                .
              </p>
              {f.can_edit && (
                <button
                  onClick={() => changesFromSpec.mutate()}
                  disabled={changesFromSpec.isPending}
                  className={`${primary} mt-2`}
                >
                  {changesFromSpec.isPending ? "Reading the edits…" : "Turn the edits into change requests"}
                </button>
              )}
            </div>
          )}
          {fromSpec && (
            <p className="text-sm text-zinc-600 dark:text-zinc-300">
              {fromSpec.created
                ? `Found ${fromSpec.created} change${fromSpec.created === 1 ? "" : "s"} in the edits.`
                : "The edits do not change what the app does, so there is nothing to request."}
            </p>
          )}
          {changesFromSpec.isError && (
            <p role="alert" className="text-sm text-rose-600">
              {errorText(changesFromSpec.error, "The edits could not be read.")}
            </p>
          )}

          {shown.length > 0 && (
            <ul className="space-y-2" aria-label="Change requests">
              {shown.map((c) => (
                <ChangeItem key={c.id} page={page} change={c} editable={f.can_edit} />
              ))}
            </ul>
          )}
          {closed.length > 0 && (
            <button onClick={() => setShowAll(!showAll)} className={quiet}>
              {showAll ? "Hide released and dismissed" : `Show ${closed.length} released or dismissed`}
            </button>
          )}

          {f.can_edit && pending.length > 0 && (
            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={() => planChanges.mutate(undefined, { onSuccess: () => changesFromSpec.reset() })}
                disabled={planChanges.isPending || building}
                className={primary}
              >
                {planChanges.isPending
                  ? "Planning…"
                  : `Plan ${pending.length} change${pending.length === 1 ? "" : "s"}`}
              </button>
              <span className="text-xs text-zinc-500">
                {building ? "A build is running; plan when it has finished." : "Adds tasks to the Tasks database."}
              </span>
            </div>
          )}
          {planned && (
            <p className="text-sm text-emerald-700 dark:text-emerald-400">
              <Icon icon={CircleCheck} className="mr-1 inline-block align-[-3px]" />Added {planned.planned} task{planned.planned === 1 ? "" : "s"} to the Tasks database. Build them in step
              5.
            </p>
          )}
          {planChanges.isError && (
            <p role="alert" className="text-sm text-rose-600">
              {errorText(planChanges.error, "The changes could not be planned.")}
            </p>
          )}

          {f.can_edit && <NewChange page={page} />}
        </div>
      )}
    </Section>
  );
}

/** The workspace's monthly token budget for builds; owners can change it. */
export function BudgetLine({ page, factory: f }: { page: Page; factory: Factory }) {
  const { setBudget } = useFactoryMutations(page.workspace_id, page.id);
  const b = f.budget;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const share = budgetShare(b);
  const out = b.limit > 0 && b.used >= b.limit;

  return (
    <div className="mb-4 text-xs text-zinc-500">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>
          Workspace budget this month:{" "}
          {b.limit > 0 ? (
            <span className={out ? "font-medium text-rose-600" : ""}>
              {tokens(b.used)} of {tokens(b.limit)} tokens used
            </span>
          ) : (
            `${tokens(b.used)} tokens used, no limit`
          )}
        </span>
        {b.limit > 0 && (
          <span
            className="h-1.5 w-24 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
            role="meter"
            aria-label="Budget used"
            aria-valuemin={0}
            aria-valuemax={b.limit}
            aria-valuenow={Math.min(b.used, b.limit)}
          >
            <span
              className={`block h-full ${share > 0.9 ? "bg-rose-500" : share > 0.7 ? "bg-amber-500" : "bg-brand-500"}`}
              style={{ width: `${Math.round(share * 100)}%` }}
            />
          </span>
        )}
        {b.can_edit && !editing && (
          <button
            onClick={() => {
              setValue(b.limit > 0 ? String(b.limit) : "0");
              setEditing(true);
            }}
            className={quiet}
          >
            Change limit
          </button>
        )}
      </div>
      {out && (
        <p className="mt-1 text-rose-600">Builds are paused until next month or until an owner raises the limit.</p>
      )}
      {editing && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setBudget.mutate(Number(value), { onSuccess: () => setEditing(false) });
          }}
          className="mt-2 flex flex-wrap items-center gap-2"
        >
          <label className="flex items-center gap-2">
            <span>Tokens per month (0 for no limit)</span>
            <input
              type="number"
              min={0}
              step={100000}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className={`${input} w-36`}
            />
          </label>
          <button type="submit" disabled={setBudget.isPending || value === ""} className={secondary}>
            Save
          </button>
          {b.custom && (
            <button
              type="button"
              onClick={() => setBudget.mutate(null, { onSuccess: () => setEditing(false) })}
              className={quiet}
            >
              Use the server default ({tokens(b.default_limit)})
            </button>
          )}
          <button type="button" onClick={() => setEditing(false)} className={quiet}>
            Cancel
          </button>
          {setBudget.isError && (
            <span role="alert" className="text-rose-600">
              {errorText(setBudget.error, "The limit could not be saved.")}
            </span>
          )}
        </form>
      )}
    </div>
  );
}
