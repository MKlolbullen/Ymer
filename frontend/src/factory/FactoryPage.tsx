import { useRef, useState } from "react";
import { Link } from "react-router";

import { DemoNote } from "@/ai/AssistMenu";
import { useAiStatus } from "@/ai/api";
import { PAGE_ICON } from "@/components/PageTree";
import { inputClass, MenuItem, Popover } from "@/components/Popover";
import { formatDateTime, timeAgo } from "@/lib/format";
import { usePages } from "@/lib/queries";
import type { Page } from "@/lib/types";

import {
  briefApproved,
  candidateSources,
  newAnswers,
  runActive,
  stepStates,
  useFactory,
  useFactoryMutations,
  type Factory,
  type FactoryQuestion,
} from "./api";
import { Build } from "./Build";
import { Deliver, Review } from "./Deliver";
import { Iterate } from "./Changes";
import { errorText, primary, quiet, secondary, Section } from "./ui";

const STEPS = [
  { key: "brief", label: "Brief" },
  { key: "architecture", label: "Architecture" },
  { key: "build", label: "Build" },
  { key: "review", label: "Review" },
  { key: "deliver", label: "Deliver" },
];

function Stepper({ factory }: { factory: Factory }) {
  const states = stepStates(factory);
  return (
    <ol className="mb-8 flex flex-wrap items-center gap-2 text-sm" aria-label="Factory stages">
      {STEPS.map((step, i) => {
        const current = states[i] === "current";
        const complete = states[i] === "done";
        const next = states[i] === "next";
        return (
          <li key={step.key} className="flex items-center gap-2">
            {i > 0 && <span aria-hidden className="h-px w-5 bg-zinc-300 dark:bg-zinc-700" />}
            <span
              aria-current={current ? "step" : undefined}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1 ${
                current
                  ? "border-brand-500 bg-brand-50 font-medium text-brand-700 dark:bg-zinc-900 dark:text-brand-500"
                  : complete
                    ? "border-emerald-500 text-emerald-700 dark:text-emerald-400"
                    : "border-zinc-200 text-zinc-400 dark:border-zinc-800"
              }`}
            >
              <span aria-hidden>{complete ? "✓" : i + 1}</span>
              {step.label}
              {next && <span className="text-xs">(coming next)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Sources({ page, factory, locked }: { page: Page; factory: Factory; locked: boolean }) {
  const pages = usePages(page.workspace_id);
  const { setSources } = useFactoryMutations(page.workspace_id, page.id);
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const editable = factory.can_edit && !locked;
  // Only visible sources are sent; the server keeps the ones this person cannot open.
  const visibleIds = factory.sources.flatMap((s) => (s.visible ? [s.page_id] : []));
  const save = (ids: string[]) => setSources.mutate(ids);

  return (
    <>
      {factory.sources.length ? (
        <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {factory.sources.map((s, i) =>
            s.visible ? (
              <li key={s.page_id} className="group flex items-center gap-2 px-3 py-2 text-sm">
                <span aria-hidden>{PAGE_ICON[s.type]}</span>
                <Link to={`/w/${page.workspace_id}/p/${s.page_id}`} className="min-w-0 flex-1 truncate hover:underline">
                  {s.title || "Untitled"}
                </Link>
                {editable && (
                  <button
                    aria-label={`Remove “${s.title || "Untitled"}”`}
                    onClick={() => save(visibleIds.filter((id) => id !== s.page_id))}
                    className="invisible px-1.5 text-zinc-400 group-hover:visible pointer-coarse:visible hover:text-rose-600 focus:visible"
                  >
                    ×
                  </button>
                )}
              </li>
            ) : (
              <li key={`hidden-${i}`} className="px-3 py-2 text-sm text-zinc-500 italic">
                A page you cannot open (left out of briefs you write)
              </li>
            ),
          )}
        </ul>
      ) : (
        <p className="text-sm text-zinc-500">No sources yet.</p>
      )}
      {editable && (
        <div className="mt-3">
          <button ref={button} onClick={() => setOpen((v) => !v)} aria-expanded={open} className={secondary}>
            + Add a page
          </button>
          {open && (
            <Popover anchor={button.current} onClose={() => setOpen(false)} width={340} label="Add a source page">
              <div className="p-2">
                <input
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find a page…"
                  aria-label="Find a page"
                  className={inputClass}
                />
              </div>
              <div className="max-h-72 overflow-y-auto p-1">
                {candidateSources(pages.data ?? [], factory, query).map((p) => (
                  <MenuItem
                    key={p.id}
                    onClick={() => {
                      save([...visibleIds, p.id]);
                      setOpen(false);
                      setQuery("");
                    }}
                  >
                    <span aria-hidden>{PAGE_ICON[p.type]}</span>
                    <span className="truncate">{p.title || "Untitled"}</span>
                  </MenuItem>
                ))}
              </div>
            </Popover>
          )}
        </div>
      )}
      {setSources.isError && (
        <p role="alert" className="mt-2 text-sm text-rose-600">
          {errorText(setSources.error, "The sources could not be saved.")}
        </p>
      )}
    </>
  );
}

function Question({ page, q, editable }: { page: Page; q: FactoryQuestion; editable: boolean }) {
  const { answer } = useFactoryMutations(page.workspace_id, page.id);
  const [draft, setDraft] = useState(q.answer ?? "");
  const [editing, setEditing] = useState(false);
  const open = q.status === "open" || editing;

  return (
    <li className={`rounded-lg border p-3 ${q.status === "open" ? "border-amber-300 dark:border-amber-700" : "border-zinc-200 dark:border-zinc-800"}`}>
      <p className={`text-sm font-medium ${q.status === "dismissed" ? "text-zinc-400 line-through" : ""}`}>{q.question}</p>
      {q.reason && <p className="mt-0.5 text-xs text-zinc-500">{q.reason}</p>}
      {open && editable ? (
        <form
          className="mt-2 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft.trim()) return;
            answer.mutate({ id: q.id, answer: draft }, { onSuccess: () => setEditing(false) });
          }}
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            maxLength={4000}
            placeholder="Your answer…"
            aria-label={`Answer: ${q.question}`}
            className={`${inputClass} resize-y`}
          />
          <div className="flex gap-1.5">
            <button type="submit" disabled={!draft.trim() || answer.isPending} className={primary}>
              Save answer
            </button>
            {q.status === "open" ? (
              <button type="button" onClick={() => answer.mutate({ id: q.id, status: "dismissed" })} className={quiet}>
                Not needed
              </button>
            ) : (
              <button type="button" onClick={() => setEditing(false)} className={quiet}>
                Cancel
              </button>
            )}
          </div>
        </form>
      ) : q.status === "answered" ? (
        <div className="mt-2 flex items-start gap-2">
          <p className="flex-1 rounded-md bg-zinc-50 px-2.5 py-1.5 text-sm dark:bg-zinc-900">{q.answer}</p>
          {editable && (
            <button onClick={() => setEditing(true)} className={quiet}>
              Edit
            </button>
          )}
        </div>
      ) : q.status === "dismissed" ? (
        <p className="mt-1 text-xs text-zinc-500">
          Marked not needed.{" "}
          {editable && (
            <button onClick={() => answer.mutate({ id: q.id, status: "open" })} className="underline hover:text-zinc-800 dark:hover:text-zinc-200">
              Reopen
            </button>
          )}
        </p>
      ) : null}
      {answer.isError && (
        <p role="alert" className="mt-1 text-xs text-rose-600">
          {errorText(answer.error, "The answer could not be saved.")}
        </p>
      )}
    </li>
  );
}

/**
 * A factory page, stage F1: choose the pages to build from, let the AI write a brief and
 * list what it still needs to know, answer that, and approve the brief.
 */
export function FactoryPage({ page }: { page: Page }) {
  const factory = useFactory(page.workspace_id, page.id);
  const status = useAiStatus();
  const { writeBrief, approve, reopen } = useFactoryMutations(page.workspace_id, page.id);

  if (factory.isLoading) return <p className="text-sm text-zinc-500">Loading…</p>;
  if (!factory.data) return <p className="text-sm text-zinc-500">Could not load the factory.</p>;
  const f = factory.data;
  const approved = briefApproved(f);
  const editable = f.can_edit && !approved;
  const open = f.questions.filter((q) => q.status === "open").length;
  const fresh = newAnswers(f);
  const aiOff = status.data && !status.data.enabled;

  return (
    <div>
      <p className="-mt-2 mb-6 text-sm text-zinc-500">
        The Factory turns the pages your team writes into a working app, one reviewed step at a time. Start with a
        brief: what to build, for whom, and what it must do.
      </p>
      <Stepper factory={f} />

      <Section n={1} title="Sources" hint="The pages to build from: specs, notes, requirement tables, mind maps and sketches.">
        <Sources page={page} factory={f} locked={approved} />
      </Section>

      <Section
        n={2}
        title="Brief"
        hint="The AI reads the sources and writes the brief as a document you can edit. What it cannot tell from them, it asks below."
      >
        {f.brief ? (
          <Link
            to={`/w/${page.workspace_id}/p/${f.brief.id}`}
            className="mb-3 flex items-center gap-3 rounded-lg border border-zinc-200 p-3 hover:border-brand-500 dark:border-zinc-800"
          >
            <span aria-hidden className="text-xl">
              📄
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{f.brief.title}</span>
              <span className="block text-xs text-zinc-500">
                Written {f.brief_generated_at ? timeAgo(f.brief_generated_at) : ""}. Open it to read or edit.
              </span>
            </span>
            <span aria-hidden className="text-zinc-400">
              →
            </span>
          </Link>
        ) : (
          <p className="mb-3 text-sm text-zinc-500">No brief yet.</p>
        )}
        {editable && !aiOff && (
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => writeBrief.mutate()}
              disabled={writeBrief.isPending || !f.sources.some((s) => s.visible)}
              className={f.brief && !fresh ? secondary : primary}
            >
              {writeBrief.isPending ? "Writing the brief…" : f.brief ? "Rewrite the brief" : "Write the brief"}
            </button>
            {f.brief && (
              <span className="text-xs text-zinc-500">
                {fresh
                  ? `${fresh} new answer${fresh === 1 ? "" : "s"} since it was written. Rewrite it to include them.`
                  : "Rewriting keeps the current brief as an earlier draft."}
              </span>
            )}
          </div>
        )}
        {aiOff && <p className="text-sm text-zinc-500">AI is turned off on this server, so the Factory cannot write briefs.</p>}
        {status.data?.demo && editable && <DemoNote className="mt-2" />}
        {writeBrief.isError && (
          <p role="alert" className="mt-2 text-sm text-rose-600">
            {errorText(writeBrief.error, "The brief could not be written.")}
          </p>
        )}

        {f.questions.length > 0 && (
          <div className="mt-6">
            <h3 className="mb-2 text-sm font-semibold">
              Questions from the AI{" "}
              <span className="font-normal text-zinc-500">
                {open ? `(${open} open)` : "(all answered)"}
              </span>
            </h3>
            <ul className="space-y-2">
              {f.questions.map((q) => (
                <Question key={`${q.id}-${q.status}`} page={page} q={q} editable={editable} />
              ))}
            </ul>
          </div>
        )}
      </Section>

      <Section n={3} title="Approve">
        {approved ? (
          <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm dark:border-emerald-800 dark:bg-emerald-950">
            <p className="font-medium text-emerald-800 dark:text-emerald-300">
              ✓ Brief approved by {f.approved_by?.display_name ?? "someone"}
              {f.approved_at ? ` on ${formatDateTime(f.approved_at)}` : ""}.
            </p>
            <p className="mt-1 text-emerald-800/80 dark:text-emerald-300/80">
              The approved text is saved as a named version of the brief. Next, design the architecture below.
            </p>
            {f.can_edit && f.stage !== "planned" && f.stage !== "built" && f.stage !== "released" && (
              <button onClick={() => reopen.mutate()} className={`${quiet} mt-2`}>
                Reopen the brief
              </button>
            )}
          </div>
        ) : (
          <>
            <p className="mb-3 text-sm text-zinc-500">
              {!f.brief
                ? "Write the brief first."
                : open
                  ? `Answer or set aside the ${open} open question${open === 1 ? "" : "s"} first.`
                  : "When the brief says what you want built, approve it. Later stages build from the approved brief."}
            </p>
            {f.can_edit && (
              <button onClick={() => approve.mutate()} disabled={!f.brief || open > 0 || approve.isPending} className={primary}>
                Approve the brief
              </button>
            )}
            {approve.isError && (
              <p role="alert" className="mt-2 text-sm text-rose-600">
                {errorText(approve.error, "The brief could not be approved.")}
              </p>
            )}
          </>
        )}
      </Section>

      <Architecture page={page} factory={f} aiOff={!!aiOff} demo={!!status.data?.demo} />
      <Build page={page} factory={f} />
      <Review page={page} factory={f} />
      <Deliver page={page} factory={f} />
      <Iterate page={page} factory={f} />
    </div>
  );
}

const PLAN_PAGES = [
  { key: "architecture", icon: "📄", label: "Architecture", note: "Stack, components, data model and interfaces" },
  { key: "system_map", icon: "🧠", label: "System map", note: "The components and the tasks in each" },
  { key: "tasks", icon: "🗃️", label: "Tasks", note: "Status, component, priority and size" },
] as const;

function Architecture({ page, factory: f, aiOff, demo }: { page: Page; factory: Factory; aiOff: boolean; demo: boolean }) {
  const { designArchitecture, approvePlan, reopen } = useFactoryMutations(page.workspace_id, page.id);
  const [stack, setStack] = useState<string | null>(f.stack);
  const open = briefApproved(f);
  const planned = f.stage === "planned" || f.stage === "built" || f.stage === "released";
  const editable = f.can_edit && open && !planned;
  const designed = !!f.architecture;

  return (
    <Section
      n={4}
      title="Architecture"
      hint={
        open
          ? "From the approved brief, the AI picks a stack and designs the app: an architecture document, a system map and the task list the build will follow."
          : "Opens once the brief is approved."
      }
    >
      {open && (
        <>
          <fieldset className="mb-4" disabled={!editable}>
            <legend className="mb-2 text-sm font-medium">Stack</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {[{ key: null, name: "Let the AI choose", summary: "It picks the stack that fits the brief and says why.", good_for: "" }, ...f.stacks].map(
                (option) => {
                  const chosen = stack === option.key;
                  return (
                    <label
                      key={option.key ?? "auto"}
                      className={`flex cursor-pointer gap-2 rounded-lg border p-3 text-sm ${
                        chosen ? "border-brand-500 bg-brand-50/50 dark:bg-zinc-900" : "border-zinc-200 dark:border-zinc-800"
                      } ${editable ? "" : "cursor-default opacity-70"}`}
                    >
                      <input
                        type="radio"
                        name="factory-stack"
                        checked={chosen}
                        onChange={() => setStack(option.key)}
                        className="mt-1 accent-brand-600"
                      />
                      <span>
                        <span className="block font-medium">
                          {option.name}
                          {option.key && option.key === f.stack && designed && (
                            <span className="ml-1.5 text-xs font-normal text-zinc-500">(current design)</span>
                          )}
                        </span>
                        <span className="block text-zinc-500">{option.summary}</span>
                        {option.good_for && <span className="block text-xs text-zinc-500">Good for: {option.good_for}</span>}
                      </span>
                    </label>
                  );
                },
              )}
            </div>
          </fieldset>

          {editable && !aiOff && (
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <button
                onClick={() => designArchitecture.mutate(stack)}
                disabled={designArchitecture.isPending}
                className={designed ? secondary : primary}
              >
                {designArchitecture.isPending
                  ? "Designing the architecture…"
                  : designed
                    ? "Redesign the architecture"
                    : "Design the architecture"}
              </button>
              {designed && <span className="text-xs text-zinc-500">Redesigning keeps the current pages as earlier drafts.</span>}
            </div>
          )}
          {demo && editable && <DemoNote className="mb-3" />}
          {designArchitecture.isError && (
            <p role="alert" className="mb-3 text-sm text-rose-600">
              {errorText(designArchitecture.error, "The architecture could not be designed.")}
            </p>
          )}

          {designed && (
            <ul className="mb-4 grid gap-2 sm:grid-cols-3">
              {PLAN_PAGES.map(({ key, icon, label, note }) => {
                const target = f[key];
                if (!target) return null;
                return (
                  <li key={key}>
                    <Link
                      to={`/w/${page.workspace_id}/p/${target.id}`}
                      className="flex h-full flex-col gap-1 rounded-lg border border-zinc-200 p-3 hover:border-brand-500 dark:border-zinc-800"
                    >
                      <span className="flex items-center gap-2 font-medium">
                        <span aria-hidden>{icon}</span>
                        {label}
                        {key === "tasks" && f.tasks && <span className="text-xs font-normal text-zinc-500">{f.tasks.count} tasks</span>}
                      </span>
                      <span className="text-xs text-zinc-500">{note}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}

          {planned ? (
            <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm dark:border-emerald-800 dark:bg-emerald-950">
              <p className="font-medium text-emerald-800 dark:text-emerald-300">
                ✓ Plan approved by {f.plan_approved_by?.display_name ?? "someone"}
                {f.plan_approved_at ? ` on ${formatDateTime(f.plan_approved_at)}` : ""}.
              </p>
              <p className="mt-1 text-emerald-800/80 dark:text-emerald-300/80">
                The architecture and system map are saved as named versions. Build it below, task by task in a
                sandbox.
              </p>
              {f.can_edit && !runActive(f.build.latest_run) && (
                <button onClick={() => reopen.mutate()} className={`${quiet} mt-2`}>
                  Reopen the plan
                </button>
              )}
            </div>
          ) : (
            designed &&
            f.can_edit && (
              <div className="flex flex-wrap items-center gap-3">
                <button onClick={() => approvePlan.mutate()} disabled={approvePlan.isPending} className={primary}>
                  Approve the plan
                </button>
                <span className="text-xs text-zinc-500">
                  Read the architecture and tasks first; edit them like any page.
                </span>
              </div>
            )
          )}
          {approvePlan.isError && (
            <p role="alert" className="mt-2 text-sm text-rose-600">
              {errorText(approvePlan.error, "The plan could not be approved.")}
            </p>
          )}
        </>
      )}
    </Section>
  );
}
