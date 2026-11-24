import { useMemo, useRef, useState, type DragEvent } from "react";
import { NavLink, useNavigate } from "react-router";

import { useCreatePage, useMovePage } from "@/lib/queries";
import { atLeast, type Page, type PageType } from "@/lib/types";

import { MenuItem, Popover } from "./Popover";

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

/** The visible ancestors of a page, root first (for breadcrumbs). */
export function ancestorsOf(pages: Page[], pageId: string): Page[] {
  const byId = new Map(pages.map((p) => [p.id, p]));
  const trail: Page[] = [];
  let current = byId.get(pageId)?.parent_id;
  while (current && byId.has(current) && trail.length < 50) {
    const page = byId.get(current)!;
    trail.unshift(page);
    current = page.parent_id;
  }
  return trail;
}

export type DropZone = "before" | "after" | "inside";

/**
 * Where a page dropped on `targetId` goes, as the move API wants it, or null when the
 * drop is not allowed (onto itself or into its own subtree).
 */
export function dropTarget(
  pages: Page[],
  draggedId: string,
  targetId: string,
  zone: DropZone,
): { parent_id: string | null; before_id: string | null } | null {
  const byId = new Map(pages.map((p) => [p.id, p]));
  const target = byId.get(targetId);
  if (!target || draggedId === targetId) return null;
  for (let p: Page | undefined = target; p; p = p.parent_id ? byId.get(p.parent_id) : undefined) {
    if (p.id === draggedId) return null; // into its own subtree
  }
  if (zone === "inside") return { parent_id: target.id, before_id: null };
  if (zone === "before") return { parent_id: target.parent_id, before_id: target.id };
  const siblings = pages
    .filter((p) => p.parent_id === target.parent_id && p.id !== draggedId)
    .sort((a, b) => a.position - b.position || a.created_at.localeCompare(b.created_at));
  const next = siblings[siblings.findIndex((p) => p.id === target.id) + 1];
  return { parent_id: target.parent_id, before_id: next?.id ?? null };
}

export const PAGE_ICON: Record<PageType, string> = { document: "📄", folder: "📁", database: "🗃️", mindmap: "🧠" };

export const NEW_PAGE_TYPES: { type: PageType; label: string }[] = [
  { type: "document", label: "Document" },
  { type: "database", label: "Database" },
  { type: "folder", label: "Folder" },
];

/** "+" that asks which kind of page to create. */
export function NewPageButton({
  workspaceId,
  parentId,
  label,
  className,
  onCreated,
}: {
  workspaceId: string;
  parentId: string | null;
  label: string;
  className: string;
  onCreated?: (page: Page) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const create = useCreatePage(workspaceId);
  const navigate = useNavigate();
  const add = async (type: PageType) => {
    setOpen(false);
    const page = await create.mutateAsync({ type, parent_id: parentId });
    onCreated?.(page);
    navigate(`/w/${workspaceId}/p/${page.id}`);
  };
  return (
    <>
      <button ref={ref} aria-label={label} title={label} onClick={() => setOpen(true)} disabled={create.isPending} className={className}>
        +
      </button>
      {open && (
        <Popover anchor={ref.current} onClose={() => setOpen(false)} width={180} label={label}>
          {NEW_PAGE_TYPES.map((t) => (
            <MenuItem key={t.type} onClick={() => void add(t.type)}>
              <span aria-hidden>{PAGE_ICON[t.type]}</span> {t.label}
            </MenuItem>
          ))}
        </Popover>
      )}
    </>
  );
}

type DragState = { dragging: string | null; setDragging: (id: string | null) => void; pages: Page[]; workspaceId: string };

function zoneFor(e: DragEvent<HTMLElement>): DropZone {
  const rect = e.currentTarget.getBoundingClientRect();
  const y = (e.clientY - rect.top) / rect.height;
  return y < 0.25 ? "before" : y > 0.75 ? "after" : "inside";
}

function TreeItem({ node, depth, drag }: { node: Node; depth: number; drag: DragState }) {
  const [open, setOpen] = useState(true);
  const [zone, setZone] = useState<DropZone | null>(null);
  const move = useMovePage(drag.workspaceId);
  const { page, children } = node;
  const canEdit = atLeast(page.my_role, "editor");
  const target = drag.dragging ? dropTarget(drag.pages, drag.dragging, page.id, zone ?? "inside") : null;
  const indicator =
    zone && target
      ? zone === "inside"
        ? "bg-brand-50 ring-1 ring-brand-500 dark:bg-zinc-800"
        : zone === "before"
          ? "shadow-[inset_0_2px_0_0_var(--color-brand-500)]"
          : "shadow-[inset_0_-2px_0_0_var(--color-brand-500)]"
      : "";

  return (
    <li>
      <div
        draggable={canEdit}
        onDragStart={(e) => {
          e.stopPropagation();
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", page.id);
          drag.setDragging(page.id);
        }}
        onDragEnd={() => drag.setDragging(null)}
        onDragOver={(e) => {
          if (!drag.dragging) return;
          const z = zoneFor(e);
          if (!dropTarget(drag.pages, drag.dragging, page.id, z)) return;
          e.preventDefault();
          setZone(z);
        }}
        onDragLeave={() => setZone(null)}
        onDrop={(e) => {
          e.preventDefault();
          const z = zoneFor(e);
          setZone(null);
          const dest = drag.dragging ? dropTarget(drag.pages, drag.dragging, page.id, z) : null;
          if (dest && drag.dragging) {
            move.mutate({ pageId: drag.dragging, ...dest });
            if (z === "inside") setOpen(true);
          }
          drag.setDragging(null);
        }}
        className={`group flex items-center rounded-md pr-1 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 ${indicator}`}
        style={{ paddingLeft: depth * 12 }}
      >
        <button
          aria-label={open ? "Collapse" : "Expand"}
          className={`flex size-6 shrink-0 items-center justify-center text-xs text-zinc-400 ${children.length ? "" : "invisible"}`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "▾" : "▸"}
        </button>
        <NavLink
          to={`/w/${drag.workspaceId}/p/${page.id}`}
          draggable={false}
          className={({ isActive }) =>
            `flex min-w-0 flex-1 items-center gap-1.5 py-1 text-sm ${isActive ? "font-medium text-zinc-900 dark:text-white" : "text-zinc-600 dark:text-zinc-400"}`
          }
        >
          <span aria-hidden>{PAGE_ICON[page.type]}</span>
          <span className="truncate">{page.title || "Untitled"}</span>
        </NavLink>
        {canEdit && (
          <NewPageButton
            workspaceId={drag.workspaceId}
            parentId={page.id}
            label={`Add a page inside ${page.title || "Untitled"}`}
            onCreated={() => setOpen(true)}
            className="invisible flex size-6 items-center justify-center rounded text-zinc-500 group-hover:visible hover:bg-zinc-300/60 dark:hover:bg-zinc-700"
          />
        )}
      </div>
      {open && children.length > 0 && (
        <ul>
          {children.map((child) => (
            <TreeItem key={child.page.id} node={child} depth={depth + 1} drag={drag} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** The sidebar tree. Editors drag pages to reorder them or drop them onto another page. */
export function PageTree({ pages, workspaceId }: { pages: Page[]; workspaceId: string }) {
  const tree = useMemo(() => buildTree(pages), [pages]);
  const [dragging, setDragging] = useState<string | null>(null);
  if (!tree.length) return <p className="px-3 py-2 text-xs text-zinc-500">No pages yet.</p>;
  const drag = { dragging, setDragging, pages, workspaceId };
  return (
    <ul aria-label="Pages">
      {tree.map((node) => (
        <TreeItem key={node.page.id} node={node} depth={0} drag={drag} />
      ))}
    </ul>
  );
}
