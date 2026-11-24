/**
 * Excalidraw <-> Yjs.
 *
 * A drawing is the Yjs map `drawing`: element id -> Excalidraw element JSON. Excalidraw
 * already versions every element (`version`, plus a random `versionNonce` to break
 * ties) and never forgets a deleted one (`isDeleted: true`), so syncing is a per-element
 * "newest wins" merge, the same rule Excalidraw's own collaboration uses:
 *
 * - local change: write each element whose version beats the stored one
 * - remote change: hand the stored elements to Excalidraw's `reconcileElements`
 *
 * Element order (z-index) travels in each element's fractional `index`.
 * The backend reads text elements for search (backend/ymer/content.py).
 *
 * This module has no Excalidraw import, so it can be unit tested without a canvas.
 */
import * as Y from "yjs";

export const MAP_NAME = "drawing";
export const LOCAL = Symbol("drawing-local");
/** Restoring a version is a local edit that the local scene must also pick up. */
export const RESTORE = Symbol("drawing-restore");

/** The fields this module relies on; everything else is carried along untouched. */
export type SyncedElement = {
  id: string;
  version: number;
  versionNonce: number;
  isDeleted?: boolean;
  index?: string | null;
  [key: string]: unknown;
};

export function elementsMap(doc: Y.Doc): Y.Map<SyncedElement> {
  return doc.getMap(MAP_NAME);
}

/** Does `a` replace `b`? Higher version wins; on a tie the lower nonce does. */
export function isNewer(a: SyncedElement, b: SyncedElement | undefined): boolean {
  if (!b) return true;
  if (a.version !== b.version) return a.version > b.version;
  return a.versionNonce < b.versionNonce;
}

/** Store the local elements that are newer than what the map holds. Returns how many. */
export function pushLocal(doc: Y.Doc, elements: readonly SyncedElement[]): number {
  const map = elementsMap(doc);
  const changed = elements.filter((el) => isNewer(el, map.get(el.id)));
  if (!changed.length) return 0;
  doc.transact(() => {
    // Excalidraw elements are immutable objects; copy so later local edits never alias.
    for (const el of changed) map.set(el.id, { ...el });
  }, LOCAL);
  return changed.length;
}

function byIndex(a: SyncedElement, b: SyncedElement): number {
  const ai = a.index ?? "";
  const bi = b.index ?? "";
  return ai < bi ? -1 : ai > bi ? 1 : a.id < b.id ? -1 : 1;
}

/** All stored elements (deleted ones too), in z-order. */
export function storedElements(doc: Y.Doc): SyncedElement[] {
  return [...elementsMap(doc).values()].sort(byIndex);
}

/**
 * Make the drawing look like an older version, as one new edit: elements that were not
 * in that version are deleted, the others come back as they were. Every touched
 * element gets a version above anything seen so far, so it wins on every client.
 */
export function restoreElements(doc: Y.Doc, old: SyncedElement[]): void {
  const map = elementsMap(doc);
  const current = [...map.values()];
  const top = Math.max(0, ...current.map((e) => e.version), ...old.map((e) => e.version)) + 1;
  const nonce = () => Math.floor(Math.random() * 2 ** 31);
  const keep = new Set(old.map((e) => e.id));
  doc.transact(() => {
    for (const el of current) {
      if (!keep.has(el.id) && !el.isDeleted) map.set(el.id, { ...el, isDeleted: true, version: top, versionNonce: nonce() });
    }
    for (const el of old) map.set(el.id, { ...el, version: top, versionNonce: nonce() });
  }, RESTORE);
}

/** Elements stored in a Yjs update (a version's state). */
export function elementsFromState(state: Uint8Array): SyncedElement[] {
  const doc = new Y.Doc();
  Y.applyUpdate(doc, state);
  const elements = storedElements(doc);
  doc.destroy();
  return elements;
}
