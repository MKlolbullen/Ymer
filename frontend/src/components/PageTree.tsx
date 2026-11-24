import { useMemo, useState } from "react";
import { NavLink, useNavigate } from "react-router";

import { useCreatePage } from "@/lib/queries";
import { atLeast, type Page, type PageType } from "@/lib/types";

type Node = { page: Page; children: Node[] };

/** Pages arrive as a flat list; nest them. Pages whose parent I cannot see become roots. */
export function buildTree(pages: Page[]): Node[] {
  const byId = new Map(pages.map((p) => [p.id, { page: p, children: [] as Node[] }]));
  const roots: Node[] = [];
  for (const node of byId.values()) {
    const parent = node.page.parent_id ? byId.get(node.page.parent_id) : undefined;
    (parent ? parent.children : roots).push(node);
  }
  const sort = (nodes: Node[]) => {
    nodes.sort((a, b) => a.page.position - b.page.position || a.page.created_at.localeCompare(b.page.created_at));
    nodes.forEach((n) => sort(n.children));
  };
  sort(roots);
  return roots;
}

const ICON: Record<PageType, string> = { document: "📄", folder: "📁", database: "🗃️", mindmap: "🧠" };

function TreeItem({ node, depth, workspaceId }: { node: Node; depth: number; workspaceId: string }) {
  const [open, setOpen] = useState(true);
  const create = useCreatePage(workspaceId);
  const navigate = useNavigate();
  const { page, children } = node;

  const addChild = async () => {
    const child = await create.mutateAsync({ type: "document", parent_id: page.id });
    setOpen(true);
    navigate(`/w/${workspaceId}/p/${child.id}`);
  };

  return (
    <li>
      <div className="group flex items-center rounded-md pr-1 hover:bg-zinc-200/60 dark:hover:bg-zinc-800" style={{ paddingLeft: depth * 12 }}>
        <button
          aria-label={open ? "Collapse" : "Expand"}
          className={`flex size-6 shrink-0 items-center justify-center text-xs text-zinc-400 ${children.length ? "" : "invisible"}`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "▾" : "▸"}
        </button>
        <NavLink
          to={`/w/${workspaceId}/p/${page.id}`}
          className={({ isActive }) =>
            `flex min-w-0 flex-1 items-center gap-1.5 py-1 text-sm ${isActive ? "font-medium text-zinc-900 dark:text-white" : "text-zinc-600 dark:text-zinc-400"}`
          }
        >
          <span aria-hidden>{ICON[page.type]}</span>
          <span className="truncate">{page.title || "Untitled"}</span>
        </NavLink>
        {atLeast(page.my_role, "editor") && (
          <button
            aria-label={`Add a page inside ${page.title || "Untitled"}`}
            title="Add a page inside"
            onClick={addChild}
            className="invisible flex size-6 items-center justify-center rounded text-zinc-500 group-hover:visible hover:bg-zinc-300/60 dark:hover:bg-zinc-700"
          >
            +
          </button>
        )}
      </div>
      {open && children.length > 0 && (
        <ul>
          {children.map((child) => (
            <TreeItem key={child.page.id} node={child} depth={depth + 1} workspaceId={workspaceId} />
          ))}
        </ul>
      )}
    </li>
  );
}

export function PageTree({ pages, workspaceId }: { pages: Page[]; workspaceId: string }) {
  const tree = useMemo(() => buildTree(pages), [pages]);
  if (!tree.length) return <p className="px-3 py-2 text-xs text-zinc-500">No pages yet.</p>;
  return (
    <ul aria-label="Pages">
      {tree.map((node) => (
        <TreeItem key={node.page.id} node={node} depth={0} workspaceId={workspaceId} />
      ))}
    </ul>
  );
}
