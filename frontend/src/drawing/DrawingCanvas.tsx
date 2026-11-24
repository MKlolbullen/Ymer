/**
 * Excalidraw bound to a Yjs document (see sync.ts), with live pointers of everyone
 * else on the page. Loaded lazily: Excalidraw is large and only drawings need it.
 */
import "@excalidraw/excalidraw/index.css";

import { CaptureUpdateAction, Excalidraw, reconcileElements } from "@excalidraw/excalidraw";
import type { OrderedExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { RemoteExcalidrawElement } from "@excalidraw/excalidraw/data/reconcile";
import type { Collaborator, ExcalidrawImperativeAPI, SocketId } from "@excalidraw/excalidraw/types";
import { useEffect, useRef, useState } from "react";
import * as Y from "yjs";

import type { CollabSession } from "@/editor/collab";

import { elementsMap, LOCAL, pushLocal, storedElements, type SyncedElement } from "./sync";

type Awareness = NonNullable<CollabSession["provider"]["awareness"]>;

type Theme = "light" | "dark";
type Me = { id: string; name: string; color: string };

// Drawings hold shapes, text and arrows. Pasted images would need file storage that
// syncs alongside, so the image tool stays off for now.
const UI_OPTIONS = {
  tools: { image: false },
  canvasActions: { loadScene: false, saveToActiveFile: false, toggleTheme: null, clearCanvas: true, export: { saveFileToDisk: true }, saveAsImage: true },
} as const;

type LiveProps = { doc: Y.Doc; awareness: Awareness | null; me: Me; readOnly: boolean; theme: Theme; name: string };

export function LiveDrawing({ doc, awareness, me, readOnly, theme, name }: LiveProps) {
  const [api, setApi] = useState<ExcalidrawImperativeAPI | null>(null);
  const pending = useRef<readonly SyncedElement[] | null>(null);
  const frame = useRef(0);

  // Remote edits (and restores) -> the scene.
  useEffect(() => {
    if (!api) return;
    const map = elementsMap(doc);
    const apply = (_events: unknown, txn: Y.Transaction) => {
      if (txn.origin === LOCAL) return;
      const remote = storedElements(doc) as unknown as RemoteExcalidrawElement[];
      const merged = reconcileElements(api.getSceneElementsIncludingDeleted(), remote, api.getAppState());
      api.updateScene({ elements: merged, captureUpdate: CaptureUpdateAction.NEVER });
    };
    api.updateScene({ elements: storedElements(doc) as unknown as OrderedExcalidrawElement[], captureUpdate: CaptureUpdateAction.NEVER });
    map.observeDeep(apply);
    return () => map.unobserveDeep(apply);
  }, [api, doc]);

  // Everyone else's pointer, from Yjs awareness.
  useEffect(() => {
    if (!api || !awareness) return;
    awareness.setLocalStateField("user", { id: me.id, name: me.name, color: me.color });
    const update = () => {
      const collaborators = new Map<SocketId, Collaborator>();
      awareness.getStates().forEach((state, clientId) => {
        if (clientId === awareness.clientID) return;
        const user = state.user as Partial<Me> | undefined;
        if (!user?.name) return;
        collaborators.set(String(clientId) as SocketId, {
          id: user.id,
          username: user.name,
          pointer: state.pointer as Collaborator["pointer"],
          button: state.button as Collaborator["button"],
          color: { background: user.color ?? "#71717a", stroke: user.color ?? "#71717a" },
        });
      });
      api.updateScene({ collaborators });
    };
    update();
    awareness.on("change", update);
    return () => awareness.off("change", update);
  }, [api, awareness, me.id, me.name, me.color]);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  return (
    <Excalidraw
      excalidrawAPI={setApi}
      theme={theme}
      name={name}
      viewModeEnabled={readOnly}
      isCollaborating
      UIOptions={UI_OPTIONS}
      onChange={(elements) => {
        if (readOnly) return;
        // onChange fires on every pointer move; push at most once per frame.
        pending.current = elements as unknown as readonly SyncedElement[];
        if (frame.current) return;
        frame.current = requestAnimationFrame(() => {
          frame.current = 0;
          if (pending.current) pushLocal(doc, pending.current);
          pending.current = null;
        });
      }}
      onPointerUpdate={({ pointer, button }) => {
        awareness?.setLocalStateField("pointer", pointer);
        awareness?.setLocalStateField("button", button);
      }}
    />
  );
}

/** A read-only drawing (version preview). */
export function StaticDrawing({ elements, theme }: { elements: SyncedElement[]; theme: Theme }) {
  return (
    <Excalidraw
      theme={theme}
      viewModeEnabled
      UIOptions={UI_OPTIONS}
      initialData={{ elements: elements as unknown as OrderedExcalidrawElement[], scrollToContent: true }}
    />
  );
}
