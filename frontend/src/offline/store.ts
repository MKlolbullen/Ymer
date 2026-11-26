/**
 * What this device keeps for working offline, per signed-in user.
 *
 * Every live page (document, mind map, drawing) the user opens gets a copy of its Yjs
 * document in IndexedDB, named `ymer-doc:<user>:<page>`. Edits made while the collab
 * service is unreachable land in that copy and are sent when the connection is back;
 * Yjs merges them with whatever other people changed meanwhile.
 *
 * Two small lists in localStorage go with it:
 * - `known`: pages whose local copy has been synced with the server at least once, so
 *   the copy is a real starting point (an empty copy of a page never seen online must
 *   not be edited, or a mind map would grow a second root on merge).
 * - `pending`: pages with local edits the server has not confirmed yet. They are pushed
 *   in the background on start-up and when the browser comes back online, and signing
 *   out warns about them.
 *
 * Signing out deletes all of it, along with the service worker's API cache.
 */
import { useSyncExternalStore } from "react";

export const API_CACHE = "ymer-api";
const DOC_PREFIX = "ymer-doc:";

type Lists = { known: string[]; pending: string[] };
type Listener = () => void;

const listeners = new Set<Listener>();
const memory = new Map<string, string>();

function storageKey(userId: string): string {
  return `ymer.offline.${userId}`;
}

function readRaw(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}

function writeRaw(key: string, value: string | null): void {
  if (value === null) memory.delete(key);
  else memory.set(key, value);
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // private mode: kept in memory for this tab
  }
}

function read(userId: string): Lists {
  try {
    const parsed = JSON.parse(readRaw(storageKey(userId)) ?? "{}") as Partial<Lists>;
    return { known: parsed.known ?? [], pending: parsed.pending ?? [] };
  } catch {
    return { known: [], pending: [] };
  }
}

const snapshots = new Map<string, Lists>();

function write(userId: string, lists: Lists): void {
  writeRaw(storageKey(userId), JSON.stringify(lists));
  snapshots.set(userId, lists);
  listeners.forEach((listener) => listener());
}

function lists(userId: string): Lists {
  let cached = snapshots.get(userId);
  if (!cached) {
    cached = read(userId);
    snapshots.set(userId, cached);
  }
  return cached;
}

function update(userId: string, change: (lists: Lists) => Lists): void {
  const before = lists(userId);
  const after = change(before);
  if (after.known !== before.known || after.pending !== before.pending) write(userId, after);
}

const add = (list: string[], id: string) => (list.includes(id) ? list : [...list, id]);
const remove = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : list);

/** Name of the IndexedDB database holding this user's copy of a page. */
export function docName(userId: string, pageId: string): string {
  return `${DOC_PREFIX}${userId}:${pageId}`;
}

export function isKnown(userId: string, pageId: string): boolean {
  return lists(userId).known.includes(pageId);
}

export function markKnown(userId: string, pageId: string): void {
  update(userId, (l) => ({ ...l, known: add(l.known, pageId) }));
}

export function markPending(userId: string, pageId: string): void {
  update(userId, (l) => ({ ...l, known: add(l.known, pageId), pending: add(l.pending, pageId) }));
}

export function clearPending(userId: string, pageId: string): void {
  update(userId, (l) => ({ ...l, pending: remove(l.pending, pageId) }));
}

export function pendingPages(userId: string): string[] {
  return lists(userId).pending;
}

/** Forget a page entirely (it was deleted, or the user lost access to it). */
export function forgetPage(userId: string, pageId: string): void {
  update(userId, (l) => ({ known: remove(l.known, pageId), pending: remove(l.pending, pageId) }));
  deleteDatabase(docName(userId, pageId));
}

if (typeof window !== "undefined") {
  // Another tab changed a list: drop the cached copy so this tab does not overwrite it.
  window.addEventListener("storage", (event) => {
    if (!event.key?.startsWith("ymer.offline.")) return;
    snapshots.delete(event.key.slice("ymer.offline.".length));
    listeners.forEach((listener) => listener());
  });
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const EMPTY: string[] = [];

/** Pages with edits waiting to reach the server, kept current for the UI. */
export function usePendingPages(userId: string | undefined): string[] {
  return useSyncExternalStore(subscribe, () => (userId ? lists(userId).pending : EMPTY));
}

function deleteDatabase(name: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      const request = indexedDB.deleteDatabase(name);
      request.onsuccess = request.onerror = request.onblocked = () => resolve();
    } catch {
      resolve();
    }
  });
}

/**
 * Delete what is kept for offline use: page copies, lists and cached API responses.
 * Signing out deletes everything (a shared computer should not keep the previous
 * user's pages); signing in as someone else deletes every other user's data.
 */
export async function wipeOfflineData({ keepUser }: { keepUser?: string } = {}): Promise<void> {
  const names = new Set<string>();
  const userIds: string[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith("ymer.offline.")) userIds.push(key.slice("ymer.offline.".length));
    }
  } catch {
    // no storage access: fall back to what this tab knows
  }
  let others = false;
  for (const id of new Set([...userIds, ...snapshots.keys()])) {
    if (id === keepUser) continue;
    others = true;
    for (const page of read(id).known) names.add(docName(id, page));
    writeRaw(storageKey(id), null);
    snapshots.delete(id);
  }
  try {
    // Also catches copies the lists lost track of (where the browser supports it).
    for (const db of (await indexedDB.databases?.()) ?? []) {
      if (!db.name?.startsWith(DOC_PREFIX)) continue;
      if (keepUser && db.name.startsWith(`${DOC_PREFIX}${keepUser}:`)) continue;
      names.add(db.name);
      others = true;
    }
  } catch {
    // indexedDB.databases() is not available everywhere
  }
  await Promise.all([...names].map(deleteDatabase));
  if (!keepUser || others) {
    try {
      await caches?.delete(API_CACHE);
    } catch {
      // Cache Storage unavailable (insecure context, private mode)
    }
  }
  listeners.forEach((listener) => listener());
}
