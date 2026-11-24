/**
 * The mind map storage format and every change to it.
 *
 * A mind map is the Yjs map `mindmap`: node id -> Y.Map
 * `{label, parent, x, y, color?, pageId?}`. Each node is its own Y.Map so that two
 * people can rename and move the same node at once without one change undoing the
 * other. `parent` is null only for the root, whose id is always `root` (so two people
 * opening an empty map at once create the same node, not two). The tree's edges come
 * from `parent`; a node whose parent was deleted by someone else meanwhile is shown
 * under the root until it is moved or deleted.
 *
 * The backend reads this format for search and backlinks (backend/ymer/content.py).
 */
import * as Y from "yjs";

export const MAP_NAME = "mindmap";
export const ROOT_ID = "root";
/** Origin of local edits, so the undo manager only undoes this person's changes. */
export const LOCAL = Symbol("mindmap-local");

export type MindNode = {
  id: string;
  label: string;
  parent: string | null;
  x: number;
  y: number;
  color: string | null;
  pageId: string | null;
};

export const BRANCH_COLORS = ["blue", "orange", "aqua", "yellow", "magenta", "green", "violet", "red"] as const;
export type BranchColor = (typeof BRANCH_COLORS)[number];

const H_GAP = 240;
const V_GAP = 64;

export function nodesMap(doc: Y.Doc): Y.Map<Y.Map<unknown>> {
  return doc.getMap(MAP_NAME);
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** Plain nodes, with orphans re-attached to the root. */
export function readNodes(map: Y.Map<Y.Map<unknown>>): MindNode[] {
  const nodes: MindNode[] = [];
  map.forEach((value, id) => {
    if (!(value instanceof Y.Map)) return;
    const label = value.get("label");
    const parent = value.get("parent");
    nodes.push({
      id,
      label: typeof label === "string" ? label : "",
      parent: id === ROOT_ID ? null : typeof parent === "string" ? parent : ROOT_ID,
      x: num(value.get("x")),
      y: num(value.get("y")),
      color: (value.get("color") as string | undefined) ?? null,
      pageId: (value.get("pageId") as string | undefined) ?? null,
    });
  });
  const ids = new Set(nodes.map((n) => n.id));
  for (const n of nodes) {
    if (n.parent && !ids.has(n.parent)) n.parent = ids.has(ROOT_ID) ? ROOT_ID : null;
  }
  return nodes;
}

/** Walk up the tree to the root's direct child, whose colour the whole branch shares. */
export function branchColor(nodes: Map<string, MindNode>, id: string): string | null {
  let node = nodes.get(id);
  const seen = new Set<string>();
  while (node && !seen.has(node.id)) {
    seen.add(node.id);
    if (node.color) return node.color;
    if (!node.parent || node.parent === ROOT_ID) break;
    node = nodes.get(node.parent);
  }
  return null;
}

export function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function nodeFields(fields: Omit<MindNode, "id">): Y.Map<unknown> {
  const m = new Y.Map<unknown>();
  m.set("label", fields.label);
  m.set("parent", fields.parent);
  m.set("x", fields.x);
  m.set("y", fields.y);
  if (fields.color) m.set("color", fields.color);
  if (fields.pageId) m.set("pageId", fields.pageId);
  return m;
}

export function ensureRoot(doc: Y.Doc, label: string): void {
  const map = nodesMap(doc);
  if (map.size > 0) return;
  doc.transact(() => {
    map.set(ROOT_ID, nodeFields({ label, parent: null, x: 0, y: 0, color: null, pageId: null }));
  }, LOCAL);
}

function children(map: Y.Map<Y.Map<unknown>>, parentId: string): MindNode[] {
  return readNodes(map).filter((n) => n.parent === parentId);
}

/** Add a child after the parent's last child; the first child of the root picks a colour. */
export function addChild(doc: Y.Doc, parentId: string, label = ""): string {
  const map = nodesMap(doc);
  const parent = readNodes(map).find((n) => n.id === parentId);
  if (!parent) return parentId;
  const siblings = children(map, parentId);
  const lowest = siblings.reduce((max, n) => Math.max(max, n.y), -Infinity);
  const id = newId();
  const color = parentId === ROOT_ID ? BRANCH_COLORS[siblings.length % BRANCH_COLORS.length]! : null;
  doc.transact(() => {
    map.set(
      id,
      nodeFields({
        label,
        parent: parentId,
        x: parent.x + H_GAP,
        y: siblings.length ? lowest + V_GAP : parent.y,
        color,
        pageId: null,
      }),
    );
  }, LOCAL);
  return id;
}

/** A sibling goes under the same parent; the root has none, so it gets a child. */
export function addSibling(doc: Y.Doc, id: string, label = ""): string {
  const node = readNodes(nodesMap(doc)).find((n) => n.id === id);
  if (!node) return id;
  return addChild(doc, node.parent ?? id, label);
}

export function descendants(nodes: MindNode[], id: string): Set<string> {
  const out = new Set<string>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const n of nodes) {
      if (n.parent && out.has(n.parent) && !out.has(n.id)) {
        out.add(n.id);
        grew = true;
      }
    }
  }
  return out;
}

/** Delete a node and everything under it. The root stays. Returns the parent id. */
export function removeNode(doc: Y.Doc, id: string): string | null {
  if (id === ROOT_ID) return null;
  const map = nodesMap(doc);
  const nodes = readNodes(map);
  const node = nodes.find((n) => n.id === id);
  if (!node) return null;
  doc.transact(() => {
    for (const gone of descendants(nodes, id)) map.delete(gone);
  }, LOCAL);
  return node.parent;
}

export function updateNode(doc: Y.Doc, id: string, patch: Partial<Omit<MindNode, "id">>): void {
  const node = nodesMap(doc).get(id);
  if (!node) return;
  doc.transact(() => {
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === undefined) node.delete(key);
      else node.set(key, value);
    }
  }, LOCAL);
}

/** Move several nodes at once (dragging a node drags its subtree). */
export function moveNodes(doc: Y.Doc, positions: { id: string; x: number; y: number }[]): void {
  const map = nodesMap(doc);
  doc.transact(() => {
    for (const { id, x, y } of positions) {
      const node = map.get(id);
      if (!node) continue;
      node.set("x", Math.round(x));
      node.set("y", Math.round(y));
    }
  }, LOCAL);
}

/** Make `id` a child of `parentId`, unless that would put it under itself. */
export function reparent(doc: Y.Doc, id: string, parentId: string): boolean {
  if (id === ROOT_ID || id === parentId) return false;
  const nodes = readNodes(nodesMap(doc));
  if (descendants(nodes, id).has(parentId)) return false;
  updateNode(doc, id, { parent: parentId });
  return true;
}

/**
 * Tidy positions: the root in the middle, its branches split between the right and
 * left side (balanced by size), each subtree stacked so siblings never overlap.
 */
export function tidyLayout(nodes: MindNode[]): Map<string, { x: number; y: number }> {
  const kids = new Map<string, MindNode[]>();
  for (const n of nodes) {
    if (!n.parent) continue;
    const list = kids.get(n.parent) ?? [];
    list.push(n);
    kids.set(n.parent, list);
  }
  for (const list of kids.values()) list.sort((a, b) => a.y - b.y || a.id.localeCompare(b.id));
  const height = new Map<string, number>();
  const measure = (id: string, seen: Set<string>): number => {
    if (seen.has(id)) return V_GAP;
    seen.add(id);
    const list = kids.get(id) ?? [];
    const h = list.length ? list.reduce((sum, c) => sum + measure(c.id, seen), 0) : V_GAP;
    height.set(id, h);
    return h;
  };
  const root = nodes.find((n) => !n.parent) ?? nodes[0];
  const out = new Map<string, { x: number; y: number }>();
  if (!root) return out;
  measure(root.id, new Set());

  const place = (id: string, x: number, top: number, dir: 1 | -1) => {
    const h = height.get(id) ?? V_GAP;
    out.set(id, { x, y: top + h / 2 - V_GAP / 2 });
    let y = top;
    for (const c of kids.get(id) ?? []) {
      if (out.has(c.id)) continue;
      place(c.id, x + dir * H_GAP, y, dir);
      y += height.get(c.id) ?? V_GAP;
    }
  };

  out.set(root.id, { x: 0, y: 0 });
  // Keep each branch on the side it is on now, then balance only if one side is empty.
  const branches = kids.get(root.id) ?? [];
  let right = branches.filter((b) => b.x >= root.x);
  let left = branches.filter((b) => b.x < root.x);
  if (!left.length && right.length > 3) {
    const total = right.reduce((s, b) => s + (height.get(b.id) ?? V_GAP), 0);
    let acc = 0;
    const keep: MindNode[] = [];
    for (const b of right) {
      if (acc < total / 2) keep.push(b);
      acc += height.get(b.id) ?? V_GAP;
    }
    left = right.filter((b) => !keep.includes(b));
    right = keep;
  }
  for (const [side, dir] of [
    [right, 1],
    [left, -1],
  ] as const) {
    const total = side.reduce((s, b) => s + (height.get(b.id) ?? V_GAP), 0);
    let y = -total / 2;
    for (const b of side) {
      place(b.id, dir * H_GAP, y, dir);
      y += height.get(b.id) ?? V_GAP;
    }
  }
  return out;
}

export function applyLayout(doc: Y.Doc): void {
  const positions = tidyLayout(readNodes(nodesMap(doc)));
  moveNodes(
    doc,
    [...positions].map(([id, p]) => ({ id, ...p })),
  );
}

/** Replace the whole map with the nodes of an older version (a normal, shared edit). */
export function restoreNodes(doc: Y.Doc, nodes: MindNode[]): void {
  const map = nodesMap(doc);
  doc.transact(() => {
    const keep = new Set(nodes.map((n) => n.id));
    for (const id of [...map.keys()]) if (!keep.has(id)) map.delete(id);
    for (const { id, ...fields } of nodes) map.set(id, nodeFields(fields));
  }, LOCAL);
}

/** Nodes stored in a base64-free Yjs update (a version's state). */
export function nodesFromState(state: Uint8Array): MindNode[] {
  const doc = new Y.Doc();
  Y.applyUpdate(doc, state);
  const nodes = readNodes(nodesMap(doc));
  doc.destroy();
  return nodes;
}
