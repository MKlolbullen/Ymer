import { HocuspocusProvider } from "@hocuspocus/provider";
import { useEffect, useRef, useState } from "react";
import { IndexeddbPersistence } from "y-indexeddb";
import * as Y from "yjs";

import { collabToken } from "@/lib/api";
import { getSession } from "@/lib/session";
import { clearPending, docName, isKnown, markKnown, markPending } from "@/offline/store";

/** The collab service is proxied at /collab in development (vite.config.ts). */
export function collabUrl(): string {
  const configured = import.meta.env.VITE_COLLAB_URL as string | undefined;
  if (configured) return configured;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/collab`;
}

export type CollabStatus = "connecting" | "connected" | "disconnected" | "denied";
export type CollabSession = { doc: Y.Doc; provider: HocuspocusProvider };
export type Notice = "comments-changed" | "versions-changed";

/** Pages open in this tab; the background sync leaves these to their own session. */
export const openPages = new Set<string>();

/**
 * One Yjs document and Hocuspocus connection per open page. A short-lived collab token
 * is fetched on every (re)connect (the login cookies are not readable from here).
 *
 * The document is also kept in IndexedDB (see offline/store.ts), so a page opened
 * before can be read and edited without a connection. `synced` is true once there is
 * real content to show: the server answered, or the local copy of a page that was
 * synced before has loaded.
 */
export function useCollabSession(pageId: string, onNotice: (notice: Notice) => void) {
  const [session, setSession] = useState<CollabSession | null>(null);
  const [status, setStatus] = useState<CollabStatus>("connecting");
  const [synced, setSynced] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  /** Whether this device has a usable copy of the page (null until IndexedDB answers). */
  const [localCopy, setLocalCopy] = useState<boolean | null>(null);
  const noticeRef = useRef(onNotice);
  noticeRef.current = onNotice;

  useEffect(() => {
    const userId = getSession()?.user.id;
    const doc = new Y.Doc();
    const persistence = userId ? new IndexeddbPersistence(docName(userId, pageId), doc) : null;
    let connected = false;
    const provider = new HocuspocusProvider({
      url: collabUrl(),
      name: pageId,
      document: doc,
      token: collabToken,
      onStatus: ({ status }) => {
        connected = status === "connected";
        if (!connected && userId && provider.hasUnsyncedChanges) markPending(userId, pageId);
        setStatus((s) => (s === "denied" ? s : (status as CollabStatus)));
      },
      onSynced: ({ state }) => {
        if (state && userId) markKnown(userId, pageId);
        if (state) setSynced(true);
      },
      onUnsyncedChanges: ({ number }) => {
        if (number === 0 && connected && provider.isSynced && userId) clearPending(userId, pageId);
      },
      onAuthenticated: ({ scope }) => {
        setReadOnly(scope === "readonly");
        // A read-only connection cannot save edits, so there is nothing left to push.
        if (scope === "readonly" && userId) clearPending(userId, pageId);
      },
      // The local copy is kept: a failed token fetch looks the same as lost access.
      onAuthenticationFailed: () => setStatus("denied"),
      onStateless: ({ payload }) => {
        try {
          const { type } = JSON.parse(payload) as { type: Notice };
          noticeRef.current(type);
        } catch {
          // ignore malformed relays
        }
      },
    });
    // Local edits that the server cannot see yet: remember the page so its copy is
    // pushed later even if this tab is closed first.
    const onUpdate = (_update: Uint8Array, origin: unknown) => {
      if (origin !== provider && origin !== persistence && !connected && userId) markPending(userId, pageId);
    };
    doc.on("update", onUpdate);
    if (persistence) {
      void persistence.whenSynced.then(() => {
        if (persistence._destroyed) return;
        const usable = !!userId && isKnown(userId, pageId);
        setLocalCopy(usable);
        if (usable) setSynced(true);
      });
    } else setLocalCopy(false);
    // The provider backs off between reconnect attempts; retry right away when the
    // browser says the network is back.
    const onOnline = () => void provider.connect();
    window.addEventListener("online", onOnline);
    openPages.add(pageId);
    setSession({ doc, provider });
    return () => {
      openPages.delete(pageId);
      window.removeEventListener("online", onOnline);
      doc.off("update", onUpdate);
      provider.destroy();
      void persistence?.destroy();
      doc.destroy();
      setSession(null);
      setSynced(false);
      setLocalCopy(null);
      setStatus("connecting");
    };
  }, [pageId]);

  return { session, status, synced, readOnly, localCopy };
}

export type CollabState = ReturnType<typeof useCollabSession>;

/** The banner text for a live page's connection, or "" when there is nothing to say. */
export function collabNotice({ status, localCopy }: Pick<CollabState, "status" | "localCopy">, online: boolean): string {
  if (status === "denied") return "You no longer have access to this page.";
  if (status === "connected") return "";
  if (!online || status === "disconnected") {
    if (localCopy === false) return "You're offline, and this page hasn't been opened on this device before, so there is no copy of it here yet.";
    return "You're offline. Edits are saved on this device and sync when you reconnect.";
  }
  return "Connecting…";
}

export function notify(session: CollabSession | null, notice: Notice): void {
  session?.provider.sendStateless(JSON.stringify({ type: notice }));
}

const COLORS = ["#e11d48", "#d97706", "#059669", "#0284c7", "#7c3aed", "#db2777", "#0d9488", "#4f46e5"];

export function colorFor(id: string): string {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return COLORS[Math.abs(hash) % COLORS.length]!;
}
