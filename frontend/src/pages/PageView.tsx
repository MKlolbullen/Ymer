import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router";

import { DocumentEditor, type Panel } from "@/editor/DocumentEditor";
import { ApiError } from "@/lib/api";
import { useDeletePage, usePage, useUpdatePage } from "@/lib/queries";
import { atLeast, type Page } from "@/lib/types";

import { useWorkspaceLayout } from "./workspace";

function TitleInput({ page }: { page: Page }) {
  const update = useUpdatePage(page.workspace_id, page.id);
  const [title, setTitle] = useState(page.title);
  useEffect(() => setTitle(page.title), [page.id, page.title]);
  const canEdit = atLeast(page.my_role, "editor");

  const save = () => {
    if (title !== page.title) update.mutate({ title: title.trim() });
  };

  return (
    <input
      aria-label="Page title"
      value={title}
      readOnly={!canEdit}
      placeholder="Untitled"
      maxLength={500}
      onChange={(e) => setTitle(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          save();
          (document.querySelector(".ProseMirror") as HTMLElement | null)?.focus();
        }
      }}
      className="mb-4 w-full bg-transparent text-3xl font-bold tracking-tight placeholder:text-zinc-300 focus:outline-none dark:placeholder:text-zinc-700"
    />
  );
}

function HeaderButton({ active, onClick, children }: { active?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className="rounded-md px-2.5 py-1 text-sm text-zinc-600 hover:bg-zinc-100 aria-pressed:bg-zinc-200 aria-pressed:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:aria-pressed:bg-zinc-700"
    >
      {children}
    </button>
  );
}

export function PageView() {
  const { pageId = "" } = useParams();
  const { workspace, setHeader } = useWorkspaceLayout();
  const navigate = useNavigate();
  const page = usePage(workspace.id, pageId);
  const remove = useDeletePage(workspace.id);
  const [panel, setPanel] = useState<Panel>(null);
  const [presence, setPresence] = useState<ReactNode>(null);

  const data = page.data;
  useEffect(() => {
    if (!data) return setHeader(null);
    const toggle = (p: Exclude<Panel, null>) => setPanel((cur) => (cur === p ? null : p));
    setHeader(
      <>
        <span className="min-w-0 flex-1 truncate text-sm text-zinc-500">{data.title || "Untitled"}</span>
        {presence}
        {data.type === "document" && (
          <>
            <HeaderButton active={panel === "comments"} onClick={() => toggle("comments")}>
              Comments
            </HeaderButton>
            <HeaderButton active={panel === "history"} onClick={() => toggle("history")}>
              History
            </HeaderButton>
          </>
        )}
        {atLeast(data.my_role, "editor") && (
          <HeaderButton
            onClick={async () => {
              if (!window.confirm(`Delete “${data.title || "Untitled"}” and every page inside it?`)) return;
              await remove.mutateAsync(data.id);
              navigate(`/w/${workspace.id}`);
            }}
          >
            Delete
          </HeaderButton>
        )}
      </>,
    );
  }, [data, panel, presence, setHeader, remove, navigate, workspace.id]);
  useEffect(() => () => setHeader(null), [setHeader]);

  if (page.isLoading) return <p className="text-sm text-zinc-500">Loading…</p>;
  if (page.isError) {
    const missing = page.error instanceof ApiError && page.error.status === 404;
    return <p className="text-sm text-zinc-500">{missing ? "This page does not exist or you do not have access to it." : "Could not load the page."}</p>;
  }
  if (!data) return null;

  return (
    <div className={`mx-auto ${panel ? "max-w-6xl" : "max-w-3xl"}`}>
      <div className={panel ? "lg:pr-86" : ""}>
        <TitleInput page={data} />
      </div>
      {data.type === "document" ? (
        <DocumentEditor key={data.id} page={data} panel={panel} onPanel={setPanel} onPresence={setPresence} />
      ) : (
        <p className="text-sm text-zinc-500">
          {data.type === "folder" ? "Folders group pages. Add pages inside it from the sidebar." : "This page type arrives in a later phase."}
        </p>
      )}
    </div>
  );
}
