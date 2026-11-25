import { HocuspocusProvider } from "@hocuspocus/provider";
import { useEffect, useRef, useState } from "react";
import * as Y from "yjs";

import { collabToken } from "@/lib/api";

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

/**
 * One Yjs document and Hocuspocus connection per open page. A short-lived collab token
 * is fetched on every (re)connect (the login cookies are not readable from here).
 */
export function useCollabSession(pageId: string, onNotice: (notice: Notice) => void) {
  const [session, setSession] = useState<CollabSession | null>(null);
  const [status, setStatus] = useState<CollabStatus>("connecting");
  const [synced, setSynced] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const noticeRef = useRef(onNotice);
  noticeRef.current = onNotice;

  useEffect(() => {
    const doc = new Y.Doc();
    const provider = new HocuspocusProvider({
      url: collabUrl(),
      name: pageId,
      document: doc,
      token: collabToken,
      onStatus: ({ status }) => setStatus((s) => (s === "denied" ? s : (status as CollabStatus))),
      onSynced: ({ state }) => setSynced(state),
      onAuthenticated: ({ scope }) => setReadOnly(scope === "readonly"),
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
    setSession({ doc, provider });
    return () => {
      provider.destroy();
      doc.destroy();
      setSession(null);
      setSynced(false);
      setStatus("connecting");
    };
  }, [pageId]);

  return { session, status, synced, readOnly };
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
