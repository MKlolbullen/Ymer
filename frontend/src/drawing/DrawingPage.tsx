/** A drawing page: a live Excalidraw canvas over the page's Yjs document. */
import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, type ReactNode } from "react";
import * as Y from "yjs";

import { useColorMode } from "@/lib/appearance";
import { colorFor, notify, useCollabSession } from "@/editor/collab";
import { Presence } from "@/editor/Presence";
import { VersionsPanel } from "@/editor/VersionsPanel";
import { useSession } from "@/lib/auth";
import { base64ToBytes, bytesToBase64 } from "@/lib/base64";
import { keys } from "@/lib/queries";
import { atLeast, type Page } from "@/lib/types";
import { CANVAS_STATUS_TEXT, type CanvasPanel } from "@/mindmap/MindMapPage";

import { elementsFromState, restoreElements } from "./sync";

// Excalidraw ships its fonts; serve them from this app rather than a CDN (vite.config.ts).
(window as { EXCALIDRAW_ASSET_PATH?: string }).EXCALIDRAW_ASSET_PATH = "/excalidraw-assets/";

const LiveDrawing = lazy(() => import("./DrawingCanvas").then((m) => ({ default: m.LiveDrawing })));
const StaticDrawing = lazy(() => import("./DrawingCanvas").then((m) => ({ default: m.StaticDrawing })));

const Loading = () => <div className="flex h-full items-center justify-center text-sm text-zinc-500">Loading the drawing…</div>;

type Props = { page: Page; panel: CanvasPanel; onPanel: (panel: CanvasPanel) => void; onPresence: (node: ReactNode) => void };

export function DrawingPage({ page, panel, onPresence }: Props) {
  const qc = useQueryClient();
  const me = useSession()!.user;
  const theme = useColorMode();
  const onNotice = useCallback(() => qc.invalidateQueries({ queryKey: keys.versions(page.workspace_id, page.id) }), [qc, page.workspace_id, page.id]);
  const collab = useCollabSession(page.id, onNotice);
  const session = collab.session;

  useEffect(() => {
    onPresence(session ? <Presence session={session} /> : null);
    return () => onPresence(null);
  }, [session, onPresence]);

  if (!session) return null;
  const canEdit = atLeast(page.my_role, "editor") && !collab.readOnly;
  return (
    <>
      {CANVAS_STATUS_TEXT[collab.status] && (
        <div className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200" role="status">
          {CANVAS_STATUS_TEXT[collab.status]}
        </div>
      )}
      <div className="flex gap-4">
        <div className="ymer-drawing h-[calc(100vh-11.5rem)] min-h-96 min-w-0 flex-1 overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
          {collab.synced ? (
            <Suspense fallback={<Loading />}>
              <LiveDrawing
                key={session.doc.guid}
                doc={session.doc}
                awareness={session.provider.awareness}
                me={{ id: me.id, name: me.display_name, color: colorFor(me.id) }}
                readOnly={!canEdit}
                theme={theme}
                name={page.title || "Drawing"}
              />
            </Suspense>
          ) : (
            <Loading />
          )}
        </div>
        {panel === "history" && (
          <aside className="fixed inset-x-0 top-14 bottom-0 z-30 overflow-hidden bg-white lg:static lg:z-auto lg:h-[calc(100vh-11.5rem)] lg:w-80 lg:shrink-0 lg:rounded-lg lg:border lg:border-zinc-200 dark:bg-zinc-950 lg:dark:border-zinc-800">
            <VersionsPanel
              workspaceId={page.workspace_id}
              pageId={page.id}
              canEdit={canEdit}
              liveState={() => bytesToBase64(Y.encodeStateAsUpdate(session.doc))}
              wide
              renderPreview={(state) => (
                <Suspense fallback={<Loading />}>
                  <div className="ymer-drawing h-full">
                    <StaticDrawing elements={elementsFromState(base64ToBytes(state))} theme={theme} />
                  </div>
                </Suspense>
              )}
              onRestore={(state) => restoreElements(session.doc, elementsFromState(base64ToBytes(state)))}
              onChanged={() => notify(session, "versions-changed")}
            />
          </aside>
        )}
      </div>
    </>
  );
}
