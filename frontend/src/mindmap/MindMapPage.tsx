/** A mind map page: a live React Flow canvas over the page's Yjs document. */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import * as Y from "yjs";

import { useTheme } from "@/charts/palette";
import { PAGE_ICON } from "@/components/PageTree";
import { inputClass, MenuItem, Popover } from "@/components/Popover";
import { colorFor, notify, useCollabSession, type CollabSession } from "@/editor/collab";
import { Presence } from "@/editor/Presence";
import { VersionsPanel } from "@/editor/VersionsPanel";
import { useSession } from "@/lib/auth";
import { base64ToBytes, bytesToBase64 } from "@/lib/base64";
import { keys, usePages } from "@/lib/queries";
import { atLeast, type Page } from "@/lib/types";

import { branchHex, MindMapCanvas, type RemoteSelection } from "./MindMapCanvas";
import {
  addChild,
  addSibling,
  applyLayout,
  BRANCH_COLORS,
  ensureRoot,
  LOCAL,
  moveNodes,
  nodesFromState,
  nodesMap,
  readNodes,
  removeNode,
  reparent,
  restoreNodes,
  ROOT_ID,
  updateNode,
  type MindNode,
} from "./model";

export type CanvasPanel = "history" | null;

type Props = { page: Page; panel: CanvasPanel; onPanel: (panel: CanvasPanel) => void; onPresence: (node: ReactNode) => void };

export const CANVAS_STATUS_TEXT = {
  connecting: "Connecting…",
  connected: "",
  disconnected: "Offline. Changes will sync when you reconnect.",
  denied: "You no longer have access to this page.",
} as const;

export function MindMapPage({ page, panel, onPanel, onPresence }: Props) {
  const qc = useQueryClient();
  const onNotice = useCallback(() => qc.invalidateQueries({ queryKey: keys.versions(page.workspace_id, page.id) }), [qc, page.workspace_id, page.id]);
  const collab = useCollabSession(page.id, onNotice);

  useEffect(() => {
    onPresence(collab.session ? <Presence session={collab.session} /> : null);
    return () => onPresence(null);
  }, [collab.session, onPresence]);

  if (!collab.session) return null;
  return (
    <>
      {CANVAS_STATUS_TEXT[collab.status] && (
        <div className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200" role="status">
          {CANVAS_STATUS_TEXT[collab.status]}
        </div>
      )}
      <MindMapEditor page={page} session={collab.session} synced={collab.synced} readOnly={collab.readOnly} panel={panel} onPanel={onPanel} />
    </>
  );
}

function useNodes(doc: Y.Doc): MindNode[] {
  const [nodes, setNodes] = useState<MindNode[]>(() => readNodes(nodesMap(doc)));
  useEffect(() => {
    const map = nodesMap(doc);
    const update = () => setNodes(readNodes(map));
    update();
    map.observeDeep(update);
    return () => map.unobserveDeep(update);
  }, [doc]);
  return nodes;
}

/** Who else has which idea selected (Yjs awareness). */
function useRemoteSelections(session: CollabSession): Map<string, RemoteSelection[]> {
  const [remote, setRemote] = useState(new Map<string, RemoteSelection[]>());
  useEffect(() => {
    const awareness = session.provider.awareness;
    if (!awareness) return;
    const update = () => {
      const next = new Map<string, RemoteSelection[]>();
      awareness.getStates().forEach((state, clientId) => {
        if (clientId === awareness.clientID) return;
        const user = state.user as { name?: string; color?: string } | undefined;
        const selected = state.selected as string | null | undefined;
        if (!user?.name || !selected) return;
        next.set(selected, [...(next.get(selected) ?? []), { name: user.name, color: user.color ?? "#71717a" }]);
      });
      setRemote(next);
    };
    update();
    awareness.on("change", update);
    return () => awareness.off("change", update);
  }, [session]);
  return remote;
}

function ToolButton({ label, onClick, disabled, children, buttonRef }: { label: string; onClick: () => void; disabled?: boolean; children: ReactNode; buttonRef?: React.Ref<HTMLButtonElement> }) {
  return (
    <button
      ref={buttonRef}
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className="rounded-md px-2 py-1 text-sm text-zinc-600 hover:bg-zinc-100 disabled:opacity-40 disabled:hover:bg-transparent dark:text-zinc-300 dark:hover:bg-zinc-800"
    >
      {children}
    </button>
  );
}

function PagePicker({ pages, current, onPick, onClose, anchor }: { pages: Page[]; current: string | null; onPick: (id: string | null) => void; onClose: () => void; anchor: HTMLElement | null }) {
  const [q, setQ] = useState("");
  const matches = pages.filter((p) => p.type !== "folder" && (p.title || "Untitled").toLowerCase().includes(q.toLowerCase())).slice(0, 12);
  return (
    <Popover anchor={anchor} onClose={onClose} width={280} label="Link a page">
      <div className="p-1">
        <input autoFocus aria-label="Find a page" placeholder="Find a page…" className={inputClass} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {current && (
        <MenuItem danger onClick={() => onPick(null)}>
          Remove link
        </MenuItem>
      )}
      {matches.map((p) => (
        <MenuItem key={p.id} onClick={() => onPick(p.id)}>
          <span aria-hidden>{PAGE_ICON[p.type]}</span> {p.title || "Untitled"}
        </MenuItem>
      ))}
      {!matches.length && <p className="px-3 py-2 text-xs text-zinc-500">No pages match.</p>}
    </Popover>
  );
}

function MindMapEditor({ page, session, synced, readOnly, panel, onPanel }: { page: Page; session: CollabSession; synced: boolean; readOnly: boolean; panel: CanvasPanel; onPanel: (p: CanvasPanel) => void }) {
  const me = useSession()!.user;
  const theme = useTheme();
  const navigate = useNavigate();
  const doc = session.doc;
  const nodes = useNodes(doc);
  const remote = useRemoteSelections(session);
  const pages = usePages(page.workspace_id);
  const canEdit = atLeast(page.my_role, "editor") && !readOnly && synced;

  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [fitKey, setFitKey] = useState(0);
  const [linking, setLinking] = useState(false);
  const linkButton = useRef<HTMLButtonElement>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  const [undo, setUndo] = useState<Y.UndoManager | null>(null);
  useEffect(() => {
    const manager = new Y.UndoManager(nodesMap(doc), { trackedOrigins: new Set([LOCAL]), captureTimeout: 400 });
    setUndo(manager);
    return () => manager.destroy();
  }, [doc]);

  // Name and colour for the presence avatars, plus which idea this person has selected.
  useEffect(() => {
    const awareness = session.provider.awareness;
    awareness?.setLocalStateField("user", { id: me.id, name: me.display_name, color: colorFor(me.id) });
  }, [session, me.id, me.display_name]);
  useEffect(() => {
    session.provider.awareness?.setLocalStateField("selected", selected);
  }, [session, selected]);

  // An empty map gets its central idea, named after the page.
  useEffect(() => {
    if (synced && canEdit) ensureRoot(doc, page.title || "Central idea");
  }, [synced, canEdit, doc, page.title]);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const current = selected ? byId.get(selected) : undefined;
  // Someone else deleted what this person had selected or was editing.
  useEffect(() => {
    if (selected && !byId.has(selected)) setSelected(null);
    if (editing && !byId.has(editing)) setEditing(null);
  }, [byId, selected, editing]);

  const focusCanvas = () => wrapper.current?.focus();
  const startEditing = (id: string, initial: string | null = null) => {
    setSelected(id);
    setDraft(initial);
    setEditing(id);
  };
  const stopEditing = () => {
    // Focus moves back right away so the next shortcut reaches the canvas.
    focusCanvas();
    setEditing(null);
    setDraft(null);
  };

  const add = (kind: "child" | "sibling") => {
    const from = selected ?? ROOT_ID;
    const id = kind === "child" ? addChild(doc, from) : addSibling(doc, from);
    startEditing(id);
  };
  const remove = () => {
    if (!selected || selected === ROOT_ID) return;
    const parent = removeNode(doc, selected);
    setSelected(parent);
  };

  const pageTitle = useCallback((id: string) => pages.data?.find((p) => p.id === id)?.title ?? null, [pages.data]);
  const openPage = useCallback((id: string) => navigate(`/w/${page.workspace_id}/p/${id}`), [navigate, page.workspace_id]);

  const siblingsOf = (id: string) => {
    const node = byId.get(id);
    return nodes.filter((n) => n.parent === node?.parent).sort((a, b) => a.y - b.y);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (editing) {
      // The idea's text box takes a frame or two to appear; keep what is typed meanwhile.
      if (e.target !== wrapper.current) return;
      e.preventDefault();
      if (e.key === "Enter" || e.key === "Escape") {
        const label = draft ?? byId.get(editing)?.label ?? "";
        if (e.key === "Enter") updateNode(doc, editing, { label: label.trim() });
        stopEditing();
      } else if (e.key === "Backspace") setDraft((d) => (d ?? "").slice(0, -1));
      else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey) setDraft((d) => (d ?? "") + e.key);
      return;
    }
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (canEdit) (e.shiftKey ? undo?.redo() : undo?.undo());
      return;
    }
    if (mod && e.key.toLowerCase() === "y") {
      e.preventDefault();
      if (canEdit) undo?.redo();
      return;
    }
    const node = selected ? byId.get(selected) : undefined;
    if (!node) {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter", "Tab"].includes(e.key)) {
        e.preventDefault();
        setSelected(ROOT_ID);
      }
      return;
    }
    switch (e.key) {
      case "Tab":
        e.preventDefault();
        if (canEdit) add("child");
        return;
      case "Enter":
        e.preventDefault();
        if (canEdit) add("sibling");
        return;
      case "Delete":
      case "Backspace":
        e.preventDefault();
        if (canEdit) remove();
        return;
      case "F2":
        e.preventDefault();
        if (canEdit) startEditing(node.id);
        return;
      case "Escape":
        setSelected(null);
        return;
      case "ArrowLeft":
      case "ArrowRight": {
        e.preventDefault();
        const towardChildren = (e.key === "ArrowRight") === (node.parent ? node.x >= (byId.get(node.parent)?.x ?? 0) : true);
        if (towardChildren) {
          const kids = nodes.filter((n) => n.parent === node.id && (node.parent || (e.key === "ArrowRight" ? n.x >= node.x : n.x < node.x)));
          const next = kids.sort((a, b) => Math.abs(a.y - node.y) - Math.abs(b.y - node.y))[0];
          if (next) setSelected(next.id);
        } else if (node.parent) setSelected(node.parent);
        return;
      }
      case "ArrowUp":
      case "ArrowDown": {
        e.preventDefault();
        const list = siblingsOf(node.id);
        const i = list.findIndex((n) => n.id === node.id);
        const next = list[i + (e.key === "ArrowDown" ? 1 : -1)];
        if (next) setSelected(next.id);
        return;
      }
    }
    // Typing on a selected idea starts editing it with what was typed.
    if (canEdit && e.key.length === 1 && !mod && !e.altKey) {
      e.preventDefault();
      startEditing(node.id, e.key);
    }
  };

  const liveState = () => bytesToBase64(Y.encodeStateAsUpdate(doc));

  return (
    <div className="flex gap-4">
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex flex-wrap items-center gap-0.5 rounded-lg border border-zinc-200 px-1 py-0.5 dark:border-zinc-800">
          {canEdit ? (
            <>
              <ToolButton label="Add a child idea (Tab)" onClick={() => add("child")}>
                + Child
              </ToolButton>
              <ToolButton label="Add a sibling idea (Enter)" disabled={!current || current.id === ROOT_ID} onClick={() => add("sibling")}>
                + Sibling
              </ToolButton>
              <ToolButton label="Rename (F2 or double-click)" disabled={!current} onClick={() => current && startEditing(current.id)}>
                Rename
              </ToolButton>
              <ToolButton label="Delete idea and everything under it (Delete)" disabled={!current || current.id === ROOT_ID} onClick={remove}>
                Delete
              </ToolButton>
              <span className="mx-1 h-5 w-px bg-zinc-200 dark:bg-zinc-800" />
              <ToolButton buttonRef={linkButton} label="Link the idea to a page" disabled={!current} onClick={() => setLinking(true)}>
                ↗ Link page
              </ToolButton>
              {current && current.id !== ROOT_ID && (
                <span className="flex items-center gap-0.5 px-1" role="group" aria-label="Branch colour">
                  {BRANCH_COLORS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      title={c}
                      aria-label={`Colour ${c}`}
                      aria-pressed={current.color === c}
                      onClick={() => updateNode(doc, current.id, { color: current.color === c ? null : c })}
                      style={{ backgroundColor: branchHex(theme, c) }}
                      className="size-4 rounded-full ring-offset-1 aria-pressed:ring-2 aria-pressed:ring-zinc-500 dark:ring-offset-zinc-950"
                    />
                  ))}
                </span>
              )}
              <span className="mx-1 h-5 w-px bg-zinc-200 dark:bg-zinc-800" />
              <ToolButton label="Undo (Ctrl+Z)" onClick={() => undo?.undo()}>
                ↶
              </ToolButton>
              <ToolButton label="Redo (Ctrl+Shift+Z)" onClick={() => undo?.redo()}>
                ↷
              </ToolButton>
              <ToolButton
                label="Tidy up the layout"
                onClick={() => {
                  applyLayout(doc);
                  setFitKey((k) => k + 1);
                }}
              >
                Tidy up
              </ToolButton>
            </>
          ) : (
            <span className="px-2 py-1 text-sm text-zinc-500">{synced ? "View only" : "Loading mind map…"}</span>
          )}
          <span className="ml-auto hidden px-2 text-xs text-zinc-400 md:inline">Tab child · Enter sibling · F2 rename · arrows move · drag onto an idea to re-parent</span>
        </div>
        <div
          ref={wrapper}
          tabIndex={0}
          onKeyDown={onKeyDown}
          aria-label="Mind map. Select an idea, then use Tab, Enter, F2, Delete and the arrow keys."
          className="h-[calc(100vh-14rem)] min-h-96 overflow-hidden rounded-lg border border-zinc-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:border-zinc-800"
        >
          <MindMapCanvas
            nodes={nodes}
            readOnly={!canEdit}
            selectedId={selected}
            editingId={editing}
            draft={draft}
            remote={remote}
            pageTitle={pageTitle}
            onOpenPage={openPage}
            fitKey={fitKey}
            onSelect={(id) => {
              setSelected(id);
              focusCanvas();
            }}
            onEdit={(id) => (id ? startEditing(id) : stopEditing())}
            onCommit={(id, label) => {
              if (byId.get(id)?.label !== label.trim()) updateNode(doc, id, { label: label.trim() });
              stopEditing();
            }}
            onTab={(id, label) => {
              updateNode(doc, id, { label: label.trim() });
              startEditing(addChild(doc, id));
            }}
            onMove={(positions) => moveNodes(doc, positions)}
            onReparent={(id, parentId) => {
              const target = byId.get(parentId);
              const node = byId.get(id);
              if (!target || !node || !reparent(doc, id, parentId)) return;
              // Hang it to the right (or left) of its new parent, below the existing children.
              const side = target.parent ? Math.sign(target.x - (byId.get(target.parent)?.x ?? 0)) || 1 : node.x >= target.x ? 1 : -1;
              const others = nodes.filter((n) => n.parent === parentId && n.id !== id);
              const y = others.length ? Math.max(...others.map((n) => n.y)) + 64 : target.y;
              const dx = target.x + side * 240 - node.x;
              const dy = y - node.y;
              const subtree = [...nodes].filter((n) => n.id === id || isUnder(byId, n.id, id));
              moveNodes(doc, subtree.map((n) => ({ id: n.id, x: n.x + dx, y: n.y + dy })));
            }}
          />
        </div>
        {linking && current && (
          <PagePicker
            anchor={linkButton.current}
            pages={(pages.data ?? []).filter((p) => p.id !== page.id)}
            current={current.pageId}
            onClose={() => setLinking(false)}
            onPick={(id) => {
              updateNode(doc, current.id, { pageId: id });
              setLinking(false);
            }}
          />
        )}
      </div>
      {panel === "history" && (
        <aside className="fixed inset-x-0 top-14 bottom-0 z-30 overflow-hidden bg-white lg:static lg:z-auto lg:h-[calc(100vh-11.5rem)] lg:w-80 lg:shrink-0 lg:rounded-lg lg:border lg:border-zinc-200 dark:bg-zinc-950 lg:dark:border-zinc-800">
          <VersionsPanel
            workspaceId={page.workspace_id}
            pageId={page.id}
            canEdit={canEdit}
            liveState={liveState}
            wide
            renderPreview={(state) => <MindMapCanvas nodes={nodesFromState(base64ToBytes(state))} readOnly />}
            onRestore={(state) => restoreNodes(doc, nodesFromState(base64ToBytes(state)))}
            onChanged={() => notify(session, "versions-changed")}
          />
          <button type="button" onClick={() => onPanel(null)} className="sr-only">
            Close history
          </button>
        </aside>
      )}
    </div>
  );
}

function isUnder(byId: Map<string, MindNode>, id: string, ancestor: string): boolean {
  const seen = new Set<string>();
  let node = byId.get(id);
  while (node?.parent && !seen.has(node.id)) {
    seen.add(node.id);
    if (node.parent === ancestor) return true;
    node = byId.get(node.parent);
  }
  return false;
}
