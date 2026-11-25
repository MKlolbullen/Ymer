/**
 * Push edits made offline for pages that are not open right now.
 *
 * Each page listed as pending (offline/store.ts) gets a short-lived connection: its local
 * copy is loaded from IndexedDB, the provider syncs it with the server (Yjs sends only
 * what the server lacks), and once the server has confirmed everything the page is
 * taken off the list. Runs one page at a time, on start-up and whenever the browser
 * comes back online.
 */
import { HocuspocusProvider } from "@hocuspocus/provider";
import { useEffect } from "react";
import { IndexeddbPersistence } from "y-indexeddb";
import * as Y from "yjs";

import { collabToken } from "@/lib/api";
import { collabUrl, openPages } from "@/editor/collab";

import { clearPending, docName, pendingPages } from "./store";

const TIMEOUT_MS = 20_000;

export type SyncResult = "synced" | "read-only" | "denied" | "timeout";

/** Sync one page's local copy with the server, then disconnect. */
export async function syncPage(userId: string, pageId: string): Promise<SyncResult> {
  const doc = new Y.Doc();
  const persistence = new IndexeddbPersistence(docName(userId, pageId), doc);
  await persistence.whenSynced;
  let provider: HocuspocusProvider | null = null;
  try {
    return await new Promise<SyncResult>((resolve) => {
      const timer = setTimeout(() => resolve("timeout"), TIMEOUT_MS);
      const finish = (result: SyncResult) => {
        clearTimeout(timer);
        resolve(result);
      };
      provider = new HocuspocusProvider({
        url: collabUrl(),
        name: pageId,
        document: doc,
        token: collabToken,
        onAuthenticated: ({ scope }) => {
          if (scope === "readonly") finish("read-only");
        },
        onAuthenticationFailed: () => finish("denied"),
        onUnsyncedChanges: ({ number }) => {
          // Give the server a moment to answer our reply to its own sync step.
          if (number === 0 && provider?.isSynced) setTimeout(() => finish("synced"), 300);
        },
      });
    });
  } finally {
    (provider as HocuspocusProvider | null)?.destroy();
    await persistence.destroy();
    doc.destroy();
  }
}

let running: Promise<void> | null = null;

/** Sync every pending page that is not open in this tab. Concurrent calls share one run. */
export function syncPendingPages(userId: string): Promise<void> {
  running ??= (async () => {
    for (const pageId of pendingPages(userId)) {
      if (openPages.has(pageId) || !navigator.onLine) continue;
      const result = await syncPage(userId, pageId);
      // On "denied" the local copy stays (access may come back), but there is no point
      // retrying it on every start.
      if (result !== "timeout") clearPending(userId, pageId);
    }
  })().finally(() => {
    running = null;
  });
  return running;
}

/** Keep pushing offline edits for the signed-in user. */
export function useBackgroundSync(userId: string | undefined): void {
  useEffect(() => {
    if (!userId) return;
    const run = () => void syncPendingPages(userId);
    run();
    window.addEventListener("online", run);
    return () => window.removeEventListener("online", run);
  }, [userId]);
}
