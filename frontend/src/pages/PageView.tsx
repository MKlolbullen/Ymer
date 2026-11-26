import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { History, MessageSquare, Share2, Trash2 } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router";

import { GenerateMenu } from "@/ai/GenerateMenu";
import { Backlinks } from "@/components/Backlinks";
import { Icon, PageIcon, type LucideIcon } from "@/components/icons";
import { ancestorsOf } from "@/components/PageTree";
import { Popover } from "@/components/Popover";
import { ShareDialog } from "@/components/ShareDialog";
import { DatabasePage } from "@/database/DatabasePage";
import { DrawingPage } from "@/drawing/DrawingPage";
import { FactoryPage } from "@/factory/FactoryPage";
import { DocumentEditor, type Panel } from "@/editor/DocumentEditor";
import { ApiError } from "@/lib/api";
import { MindMapPage } from "@/mindmap/MindMapPage";
import { PageMenu } from "@/templates/PageMenu";
import { useCreatePage, useDeletePage, usePage, usePages, useUpdatePage } from "@/lib/queries";
import { atLeast, LIVE_PAGE_TYPES, type Page, type PageType } from "@/lib/types";

import { useWorkspaceLayout } from "./workspace";

function TitleInput({ page }: { page: Page }) {
  const update = useUpdatePage(page.workspace_id, page.id);
  const [title, setTitle] = useState(page.title);
  useEffect(() => setTitle(page.title), [page.id, page.title]);
  const canEdit = atLeast(page.my_role, "editor");

  const save = () => {
    if (title !== page.title) update.mutate({ title: title.trim() });
  };

  // Grow with the text so long titles wrap instead of scrolling sideways on phones.
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [title]);

  return (
    <textarea
      ref={ref}
      rows={1}
      aria-label="Page title"
      value={title}
      readOnly={!canEdit}
      placeholder="Untitled"
      maxLength={500}
      onChange={(e) => setTitle(e.target.value.replace(/\n/g, " "))}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          save();
          (document.querySelector(".ProseMirror") as HTMLElement | null)?.focus();
        }
      }}
      className="mb-4 block w-full resize-none overflow-hidden bg-transparent text-2xl font-bold tracking-tight placeholder:text-zinc-300 focus:outline-none sm:text-3xl dark:placeholder:text-zinc-700"
    />
  );
}

function Breadcrumbs({ page }: { page: Page }) {
  const pages = usePages(page.workspace_id);
  const trail = ancestorsOf(pages.data ?? [], page.id);
  return (
    <nav aria-label="Breadcrumbs" className="flex min-w-0 flex-1 items-center gap-1 truncate text-sm text-zinc-500">
      {trail.map((p) => (
        <span key={p.id} className="hidden min-w-0 items-center gap-1 sm:flex">
          <Link to={`/w/${page.workspace_id}/p/${p.id}`} className="max-w-40 truncate hover:text-zinc-900 dark:hover:text-white">
            {p.title || "Untitled"}
          </Link>
          <span aria-hidden>/</span>
        </span>
      ))}
      <span className="truncate text-zinc-700 dark:text-zinc-300">{page.title || "Untitled"}</span>
    </nav>
  );
}

function ShareButton({ page }: { page: Page }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        ref={ref}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Share"
        title="Share"
        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-zinc-600 hover:bg-zinc-100 sm:px-2.5 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        <Label icon={Share2} text="Share" />
      </button>
      {open && (
        <Popover anchor={ref.current} onClose={() => setOpen(false)} width={380} label="Share">
          <ShareDialog page={page} />
        </Popover>
      )}
    </>
  );
}

/** A folder lists what is inside it. */
function FolderView({ page }: { page: Page }) {
  const pages = usePages(page.workspace_id);
  const create = useCreatePage(page.workspace_id);
  const navigate = useNavigate();
  const children = (pages.data ?? []).filter((p) => p.parent_id === page.id).sort((a, b) => a.position - b.position);
  const add = async (type: PageType) => {
    const child = await create.mutateAsync({ type, parent_id: page.id });
    navigate(`/w/${page.workspace_id}/p/${child.id}`);
  };
  return (
    <div className="space-y-4">
      {children.length ? (
        <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {children.map((child) => (
            <li key={child.id}>
              <Link to={`/w/${page.workspace_id}/p/${child.id}`} className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-50 dark:hover:bg-zinc-900">
                <PageIcon type={child.type} className="text-zinc-500" />
                {child.title || "Untitled"}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-zinc-500">This folder is empty.</p>
      )}
      {atLeast(page.my_role, "editor") && (
        <div className="flex gap-2">
          <button onClick={() => void add("document")} className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900">
            <PageIcon type="document" className="text-zinc-500" /> Document
          </button>
          <button onClick={() => void add("database")} className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900">
            <PageIcon type="database" className="text-zinc-500" /> Database
          </button>
          <button onClick={() => void add("mindmap")} className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900">
            <PageIcon type="mindmap" className="text-zinc-500" /> Mind map
          </button>
          <button onClick={() => void add("drawing")} className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900">
            <PageIcon type="drawing" className="text-zinc-500" /> Drawing
          </button>
        </div>
      )}
    </div>
  );
}

/** A header action: an icon, plus the word on wider screens. */
function Label({ icon, text }: { icon: LucideIcon; text: string }) {
  return (
    <>
      <Icon icon={icon} />
      <span className="hidden sm:inline">{text}</span>
    </>
  );
}

function HeaderButton({ active, onClick, icon, label }: { active?: boolean; onClick: () => void; icon: LucideIcon; label: string }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      aria-label={label}
      title={label}
      className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm whitespace-nowrap text-zinc-600 hover:bg-zinc-100 aria-pressed:bg-zinc-200 aria-pressed:text-zinc-900 sm:px-2.5 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:aria-pressed:bg-zinc-700 dark:aria-pressed:text-white"
    >
      <Label icon={icon} text={label} />
    </button>
  );
}

export function PageView() {
  const { pageId = "" } = useParams();
  const { workspace, setHeader } = useWorkspaceLayout();
  const navigate = useNavigate();
  const page = usePage(workspace.id, pageId);
  // mutateAsync is stable; the mutation object itself is new on every render.
  const { mutateAsync: deletePage } = useDeletePage(workspace.id);
  const [panel, setPanel] = useState<Panel>(null);
  const [presence, setPresence] = useState<ReactNode>(null);

  // Offline, a page never opened here has no cached response of its own; the sidebar's
  // page list still knows it.
  const list = usePages(workspace.id);
  const data = page.data ?? list.data?.find((p) => p.id === pageId);
  useEffect(() => {
    if (!data) return setHeader(null);
    const toggle = (p: Exclude<Panel, null>) => setPanel((cur) => (cur === p ? null : p));
    setHeader(
      <>
        <Breadcrumbs page={data} />
        {presence}
        <div className="flex shrink-0 items-center sm:gap-1">
          {data.type === "document" && (
            <HeaderButton active={panel === "comments"} onClick={() => toggle("comments")} icon={MessageSquare} label="Comments" />
          )}
          {LIVE_PAGE_TYPES.includes(data.type) && (
            <HeaderButton active={panel === "history"} onClick={() => toggle("history")} icon={History} label="History" />
          )}
          <GenerateMenu page={data} />
          {atLeast(data.my_role, "editor") && <ShareButton page={data} />}
          <PageMenu page={data} />
          {atLeast(data.my_role, "editor") && (
            <HeaderButton
              icon={Trash2}
              label="Delete"
              onClick={async () => {
                if (!window.confirm(`Delete “${data.title || "Untitled"}” and every page inside it?`)) return;
                await deletePage(data.id);
                navigate(`/w/${workspace.id}`);
              }}
            />
          )}
        </div>
      </>,
    );
  }, [data, panel, presence, setHeader, deletePage, navigate, workspace.id]);
  useEffect(() => () => setHeader(null), [setHeader]);

  if (!data && page.isLoading) return <p className="text-sm text-zinc-500">Loading…</p>;
  if (page.isError && !data) {
    const missing = page.error instanceof ApiError && page.error.status === 404;
    return <p className="text-sm text-zinc-500">{missing ? "This page does not exist or you do not have access to it." : "Could not load the page."}</p>;
  }
  if (!data) return null;

  if (data.type === "mindmap" || data.type === "drawing") {
    const Canvas = data.type === "mindmap" ? MindMapPage : DrawingPage;
    return (
      <div className="mx-auto max-w-[96rem]">
        <TitleInput page={data} />
        <Canvas
          key={data.id}
          page={data}
          panel={panel === "history" ? "history" : null}
          onPanel={setPanel}
          onPresence={setPresence}
        />
      </div>
    );
  }

  if (data.type === "factory") {
    return (
      <div className="mx-auto max-w-4xl">
        <TitleInput page={data} />
        <FactoryPage key={data.id} page={data} />
      </div>
    );
  }

  if (data.type === "database") {
    return (
      <div className="mx-auto max-w-7xl">
        <TitleInput page={data} />
        <DatabasePage key={data.id} page={data} />
      </div>
    );
  }

  return (
    <div className={`mx-auto ${panel ? "max-w-6xl" : "max-w-3xl"}`}>
      <div className={panel ? "lg:pr-86" : ""}>
        <TitleInput page={data} />
      </div>
      {data.type === "document" ? (
        <>
          <DocumentEditor key={data.id} page={data} panel={panel} onPanel={setPanel} onPresence={setPresence} />
          <div className={panel ? "lg:pr-86" : ""}>
            <Backlinks workspaceId={workspace.id} pageId={data.id} />
          </div>
        </>
      ) : data.type === "folder" ? (
        <FolderView page={data} />
      ) : null}
    </div>
  );
}
