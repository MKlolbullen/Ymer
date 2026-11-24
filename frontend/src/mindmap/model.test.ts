import { expect, test } from "vitest";
import * as Y from "yjs";

import {
  addChild,
  addSibling,
  BRANCH_COLORS,
  ensureRoot,
  nodesFromState,
  nodesMap,
  readNodes,
  removeNode,
  reparent,
  restoreNodes,
  ROOT_ID,
  tidyLayout,
  updateNode,
} from "./model";

function fresh() {
  const doc = new Y.Doc();
  ensureRoot(doc, "Plan");
  return doc;
}

const byId = (doc: Y.Doc) => new Map(readNodes(nodesMap(doc)).map((n) => [n.id, n]));

test("an empty map gets one root, even if two people create it", () => {
  const a = fresh();
  const b = fresh();
  Y.applyUpdate(a, Y.encodeStateAsUpdate(b));
  expect(readNodes(nodesMap(a)).map((n) => n.id)).toEqual([ROOT_ID]);
  ensureRoot(a, "again");
  expect(byId(a).get(ROOT_ID)!.label).toBe("Plan");
});

test("children go to the right of their parent and branches get colours in order", () => {
  const doc = fresh();
  const one = addChild(doc, ROOT_ID, "One");
  const two = addChild(doc, ROOT_ID, "Two");
  const leaf = addChild(doc, one, "Leaf");
  const nodes = byId(doc);
  expect(nodes.get(one)).toMatchObject({ parent: ROOT_ID, x: 240, color: BRANCH_COLORS[0] });
  expect(nodes.get(two)!.color).toBe(BRANCH_COLORS[1]);
  expect(nodes.get(two)!.y).toBeGreaterThan(nodes.get(one)!.y);
  expect(nodes.get(leaf)).toMatchObject({ parent: one, x: 480, color: null });
  const sibling = addSibling(doc, leaf);
  expect(byId(doc).get(sibling)!.parent).toBe(one);
});

test("removing a node removes its subtree but never the root", () => {
  const doc = fresh();
  const one = addChild(doc, ROOT_ID);
  addChild(doc, addChild(doc, one));
  const keep = addChild(doc, ROOT_ID);
  expect(removeNode(doc, ROOT_ID)).toBeNull();
  expect(removeNode(doc, one)).toBe(ROOT_ID);
  expect([...byId(doc).keys()].sort()).toEqual([keep, ROOT_ID].sort());
});

test("orphans of a concurrent delete hang off the root", () => {
  const a = fresh();
  const parent = addChild(a, ROOT_ID);
  const b = new Y.Doc();
  Y.applyUpdate(b, Y.encodeStateAsUpdate(a));
  const child = addChild(b, parent); // b adds under parent...
  removeNode(a, parent); // ...while a deletes it
  Y.applyUpdate(a, Y.encodeStateAsUpdate(b));
  Y.applyUpdate(b, Y.encodeStateAsUpdate(a));
  expect(byId(a).get(child)!.parent).toBe(ROOT_ID);
});

test("concurrent rename and move of one node both survive", () => {
  const a = fresh();
  const id = addChild(a, ROOT_ID, "Old");
  const b = new Y.Doc();
  Y.applyUpdate(b, Y.encodeStateAsUpdate(a));
  updateNode(a, id, { label: "New" });
  updateNode(b, id, { x: 999 });
  Y.applyUpdate(a, Y.encodeStateAsUpdate(b));
  expect(byId(a).get(id)).toMatchObject({ label: "New", x: 999 });
});

test("reparent refuses to move a node under itself", () => {
  const doc = fresh();
  const one = addChild(doc, ROOT_ID);
  const two = addChild(doc, one);
  expect(reparent(doc, one, two)).toBe(false);
  expect(reparent(doc, ROOT_ID, one)).toBe(false);
  const other = addChild(doc, ROOT_ID);
  expect(reparent(doc, two, other)).toBe(true);
  expect(byId(doc).get(two)!.parent).toBe(other);
});

test("tidy layout keeps siblings apart and children beside their parent", () => {
  const doc = fresh();
  const a = addChild(doc, ROOT_ID);
  const b = addChild(doc, ROOT_ID);
  const a1 = addChild(doc, a);
  const a2 = addChild(doc, a);
  const layout = tidyLayout(readNodes(nodesMap(doc)));
  expect(layout.get(ROOT_ID)).toEqual({ x: 0, y: 0 });
  expect(layout.get(a1)!.x).toBe(layout.get(a)!.x + 240);
  expect(layout.get(a2)!.y - layout.get(a1)!.y).toBeGreaterThanOrEqual(64);
  expect(layout.get(b)!.y).toBeGreaterThan(layout.get(a2)!.y - 64);
});

test("restoring an old version replaces the map's content", () => {
  const doc = fresh();
  addChild(doc, ROOT_ID, "Kept");
  const old = nodesFromState(Y.encodeStateAsUpdate(doc));
  addChild(doc, ROOT_ID, "Added later");
  restoreNodes(doc, old);
  expect(readNodes(nodesMap(doc)).map((n) => n.label).sort()).toEqual(["Kept", "Plan"]);
});
