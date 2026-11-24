import { expect, test } from "vitest";
import * as Y from "yjs";

import { elementsFromState, elementsMap, isNewer, pushLocal, restoreElements, storedElements, type SyncedElement } from "./sync";

const el = (id: string, version: number, extra: Partial<SyncedElement> = {}): SyncedElement => ({
  id,
  version,
  versionNonce: 5,
  index: id,
  type: "rectangle",
  ...extra,
});

test("newer versions win and ties go to the lower nonce", () => {
  expect(isNewer(el("a", 2), el("a", 1))).toBe(true);
  expect(isNewer(el("a", 1), el("a", 2))).toBe(false);
  expect(isNewer(el("a", 1, { versionNonce: 1 }), el("a", 1, { versionNonce: 9 }))).toBe(true);
  expect(isNewer(el("a", 1), el("a", 1))).toBe(false);
});

test("pushLocal stores only what changed", () => {
  const doc = new Y.Doc();
  expect(pushLocal(doc, [el("a", 1), el("b", 1)])).toBe(2);
  expect(pushLocal(doc, [el("a", 1), el("b", 2)])).toBe(1);
  expect(pushLocal(doc, [el("a", 0)])).toBe(0); // stale local copy never overwrites
  expect(elementsMap(doc).get("b")!.version).toBe(2);
});

test("two people drawing at once converge, deletions included", () => {
  const a = new Y.Doc();
  const b = new Y.Doc();
  pushLocal(a, [el("shared", 1)]);
  Y.applyUpdate(b, Y.encodeStateAsUpdate(a));
  pushLocal(a, [el("shared", 2, { isDeleted: true }), el("fromA", 1)]);
  pushLocal(b, [el("fromB", 1)]);
  Y.applyUpdate(a, Y.encodeStateAsUpdate(b));
  Y.applyUpdate(b, Y.encodeStateAsUpdate(a));
  expect(storedElements(a)).toEqual(storedElements(b));
  expect(storedElements(b).map((e) => e.id)).toEqual(["fromA", "fromB", "shared"]);
  expect(elementsMap(b).get("shared")!.isDeleted).toBe(true);
});

test("restoring a version deletes later elements and wins over current versions", () => {
  const doc = new Y.Doc();
  pushLocal(doc, [el("old", 3)]);
  const version = elementsFromState(Y.encodeStateAsUpdate(doc));
  pushLocal(doc, [el("old", 7, { x: 10 }), el("new", 1)]);
  restoreElements(doc, version);
  const map = elementsMap(doc);
  expect(map.get("new")!.isDeleted).toBe(true);
  expect(map.get("old")!.version).toBeGreaterThan(7);
  expect(map.get("old")!.x).toBeUndefined();
});
