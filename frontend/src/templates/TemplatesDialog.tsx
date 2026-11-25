import { useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router";

import { PAGE_ICON } from "@/components/PageTree";
import { usePages } from "@/lib/queries";
import type { Page } from "@/lib/types";

import {
  describeSummary,
  importable,
  useApplyTemplate,
  useImport,
  useTemplates,
  type ImportSummary,
  type Template,
} from "./api";

export type TemplatesTab = "templates" | "import";

type Props = {
  workspaceId: string;
  /** Where new pages go: inside this page, or at the top level. */
  parentId: string | null;
  initialTab?: TemplatesTab;
  onClose: () => void;
};

const tabClass = (active: boolean) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${
    active ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-white" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
  }`;

function Card({ icon, name, description, onClick, disabled }: { icon: string; name: string; description: string; onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex items-start gap-3 rounded-lg border border-zinc-200 p-3 text-left hover:border-brand-500 hover:bg-brand-50/40 disabled:opacity-60 dark:border-zinc-800 dark:hover:bg-zinc-800/60"
    >
      <span aria-hidden className="text-xl leading-none">
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium">{name}</span>
        <span className="block text-xs text-zinc-500">{description}</span>
      </span>
    </button>
  );
}

function Warnings({ summary }: { summary: ImportSummary }) {
  if (!summary.warnings.length) return null;
  return (
    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-700 dark:text-amber-400">
      {summary.warnings.map((w) => (
        <li key={w}>{w}</li>
      ))}
    </ul>
  );
}

function TemplatesTabView({ workspaceId, parentId, onDone }: { workspaceId: string; parentId: string | null; onDone: (page: Page) => void }) {
  const catalog = useTemplates(workspaceId);
  const apply = useApplyTemplate(workspaceId);
  const [error, setError] = useState<string | null>(null);
  const use = async (body: { template_id?: string; page_id?: string }) => {
    setError(null);
    try {
      const result = await apply.mutateAsync({ ...body, parent_id: parentId });
      if (result.page) onDone(result.page);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not use that template.");
    }
  };
  if (catalog.isLoading) return <p className="p-4 text-sm text-zinc-500">Loading…</p>;
  if (!catalog.data) return <p className="p-4 text-sm text-zinc-500">Could not load templates.</p>;
  const custom = catalog.data.custom ?? [];
  const grid = (items: Template[]) => (
    <div className="grid gap-2 sm:grid-cols-2">
      {items.map((t) => (
        <Card key={t.id} icon={t.icon} name={t.name} description={t.description} disabled={apply.isPending} onClick={() => void use({ template_id: t.id })} />
      ))}
    </div>
  );
  return (
    <div className="space-y-5 p-4">
      {error && <p className="text-sm text-rose-600">{error}</p>}
      {custom.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-medium tracking-wide text-zinc-500 uppercase">This workspace's templates</h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {custom.map((p) => (
              <Card
                key={p.id}
                icon={PAGE_ICON[p.type]}
                name={p.title || "Untitled"}
                description="Saved as a template. A copy is added, with everything inside it."
                disabled={apply.isPending}
                onClick={() => void use({ page_id: p.id })}
              />
            ))}
          </div>
        </section>
      )}
      <section>
        <h3 className="mb-2 text-xs font-medium tracking-wide text-zinc-500 uppercase">Pages</h3>
        {grid(catalog.data.page)}
      </section>
      <section>
        <h3 className="mb-2 text-xs font-medium tracking-wide text-zinc-500 uppercase">Starter sets</h3>
        {grid(catalog.data.workspace)}
      </section>
      <p className="text-xs text-zinc-500">
        To make your own, open a page and choose <strong>Save as template</strong> in its <strong>⋯</strong> menu.
      </p>
    </div>
  );
}

function ImportTabView({ workspaceId, parentId, onDone }: { workspaceId: string; parentId: string | null; onDone: (page: Page) => void }) {
  const run = useImport(workspaceId);
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ pages: Page[]; summary: ImportSummary } | null>(null);

  const add = (list: FileList | null) => {
    const picked = Array.from(list ?? []);
    const ok = picked.filter(importable);
    setError(ok.length < picked.length ? "Only .zip, .md and .csv files can be imported." : null);
    setFiles((current) => [...current, ...ok]);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    add(e.dataTransfer.files);
  };
  const start = async () => {
    setError(null);
    try {
      setDone(await run.mutateAsync({ files, parentId }));
      setFiles([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The import failed.");
    }
  };

  if (done) {
    return (
      <div className="space-y-3 p-4">
        <p className="text-sm font-medium">{describeSummary(done.summary)}</p>
        <Warnings summary={done.summary} />
        <div className="flex flex-wrap gap-2">
          {done.pages.slice(0, 6).map((p) => (
            <button key={p.id} onClick={() => onDone(p)} className="rounded-md border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
              {PAGE_ICON[p.type]} Open {p.title || "Untitled"}
            </button>
          ))}
          <button onClick={() => setDone(null)} className="px-2 text-sm text-zinc-500">
            Import more
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center ${
          over ? "border-brand-500 bg-brand-50/50 dark:bg-zinc-800" : "border-zinc-300 dark:border-zinc-700"
        }`}
      >
        <span aria-hidden className="text-3xl">
          📥
        </span>
        <p className="text-sm">Drop a Notion export, Markdown files or a CSV here</p>
        <button type="button" onClick={() => input.current?.click()} className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-500">
          or choose files
        </button>
        <input ref={input} type="file" multiple accept=".zip,.md,.markdown,.csv" aria-label="Files to import" className="hidden" onChange={(e) => add(e.target.files)} />
      </div>
      {files.length > 0 && (
        <ul className="space-y-1 text-sm">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center gap-2">
              <span aria-hidden>{f.name.toLowerCase().endsWith(".zip") ? "🗜️" : f.name.toLowerCase().endsWith(".csv") ? "🗃️" : "📄"}</span>
              <span className="truncate">{f.name}</span>
              <span className="text-xs text-zinc-500">{Math.max(1, Math.round(f.size / 1024))} KB</span>
              <button aria-label={`Remove ${f.name}`} onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))} className="ml-auto text-xs text-zinc-500 hover:text-rose-600">
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="text-sm text-rose-600">{error}</p>}
      <div className="flex items-center gap-3">
        <button
          onClick={() => void start()}
          disabled={!files.length || run.isPending}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-on-brand hover:bg-brand-700 disabled:opacity-60"
        >
          {run.isPending ? "Importing…" : "Import"}
        </button>
      </div>
      <details className="text-xs text-zinc-500">
        <summary className="cursor-pointer">What gets imported, and how to export from Notion</summary>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            In Notion, open <em>Settings → Export</em> (or a page's <em>⋯ → Export</em>), choose <em>Markdown &amp; CSV</em> with sub-pages, and drop the zip here as it is.
          </li>
          <li>Folders become the page tree. Markdown files become documents, CSV files become databases with typed columns.</li>
          <li>Links between imported pages become page links (with backlinks), and linked images and files are attached.</li>
          <li>Obsidian-style [[wiki links]] work too. Tables become text blocks for now.</li>
        </ul>
      </details>
    </div>
  );
}

/** Templates and import, for adding ready-made or outside content to a workspace. */
export function TemplatesDialog({ workspaceId, parentId, initialTab = "templates", onClose }: Props) {
  const navigate = useNavigate();
  const pages = usePages(workspaceId);
  const [tab, setTab] = useState<TemplatesTab>(initialTab);
  const parent = parentId ? pages.data?.find((p) => p.id === parentId) : undefined;
  const open = (page: Page) => {
    onClose();
    navigate(`/w/${workspaceId}/p/${page.id}`);
  };
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[8vh]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label="Templates and import"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
        className="flex max-h-[84vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900"
      >
        <div className="flex items-center gap-1 border-b border-zinc-200 px-3 py-2 dark:border-zinc-700">
          <button className={tabClass(tab === "templates")} aria-pressed={tab === "templates"} onClick={() => setTab("templates")}>
            Templates
          </button>
          <button className={tabClass(tab === "import")} aria-pressed={tab === "import"} onClick={() => setTab("import")}>
            Import
          </button>
          <span className="ml-auto truncate pl-2 text-xs text-zinc-500">
            Adds to: {parent ? `${PAGE_ICON[parent.type]} ${parent.title || "Untitled"}` : "top level"}
          </span>
          <button aria-label="Close" onClick={onClose} className="ml-2 rounded px-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            ✕
          </button>
        </div>
        <div className="overflow-y-auto">
          {tab === "templates" ? (
            <TemplatesTabView workspaceId={workspaceId} parentId={parentId} onDone={open} />
          ) : (
            <ImportTabView workspaceId={workspaceId} parentId={parentId} onDone={open} />
          )}
        </div>
      </div>
    </div>
  );
}
