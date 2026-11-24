import { useEffect, useState } from "react";

import type { CollabSession } from "./collab";

type Peer = { clientId: number; name: string; color: string };

/** Avatars of everyone with this document open (from Yjs awareness). */
export function Presence({ session }: { session: CollabSession }) {
  const [peers, setPeers] = useState<Peer[]>([]);

  useEffect(() => {
    const awareness = session.provider.awareness;
    if (!awareness) return;
    const update = () => {
      const seen = new Set<string>();
      const list: Peer[] = [];
      awareness.getStates().forEach((state, clientId) => {
        const user = state.user as { name?: string; color?: string; id?: string } | undefined;
        if (!user?.name) return;
        const key = user.id ?? String(clientId);
        if (seen.has(key)) return; // same person in two tabs
        seen.add(key);
        list.push({ clientId, name: user.name, color: user.color ?? "#71717a" });
      });
      setPeers(list);
    };
    update();
    awareness.on("change", update);
    return () => awareness.off("change", update);
  }, [session]);

  return (
    <div className="flex -space-x-1.5" aria-label={`${peers.length} people here`}>
      {peers.slice(0, 6).map((p) => (
        <span
          key={p.clientId}
          title={p.name}
          className="inline-flex size-7 items-center justify-center rounded-full text-xs font-semibold text-white ring-2 ring-white dark:ring-zinc-950"
          style={{ backgroundColor: p.color }}
        >
          {p.name.slice(0, 1).toUpperCase()}
        </span>
      ))}
      {peers.length > 6 && (
        <span className="inline-flex size-7 items-center justify-center rounded-full bg-zinc-300 text-xs ring-2 ring-white">
          +{peers.length - 6}
        </span>
      )}
    </div>
  );
}
