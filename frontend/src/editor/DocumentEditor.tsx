import { useQueryClient } from "@tanstack/react-query";
import Collaboration from "@tiptap/extension-collaboration";
import CollaborationCaret from "@tiptap/extension-collaboration-caret";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import { useCallback, useEffect, useRef, useState } from "react";
import * as Y from "yjs";

import { useSession } from "@/lib/auth";
import { bytesToBase64 } from "@/lib/base64";
import { keys, uploadFile, useComments } from "@/lib/queries";
import { atLeast, type Comment, type Page } from "@/lib/types";

import { anchorFromSelection, CommentHighlights, refreshHighlights, resolveAnchor } from "./anchors";
import { colorFor, notify, useCollabSession, type CollabSession, type Notice } from "./collab";
import { CommentsPanel, type Draft } from "./CommentsPanel";
import { baseExtensions } from "./extensions";
import { Presence } from "./Presence";
import { Toolbar } from "./Toolbar";
import { VersionsPanel } from "./VersionsPanel";

export type Panel = "comments" | "history" | null;

type Props = { page: Page; panel: Panel; onPanel: (panel: Panel) => void; onPresence: (node: React.ReactNode) => void };

const STATUS_TEXT = {
  connecting: "Connecting…",
  connected: "",
  disconnected: "Offline. Changes will sync when you reconnect.",
  denied: "You no longer have access to this page.",
} as const;

/** A document page: live collaborative editor plus the comments and history panels. */
export function DocumentEditor({ page, panel, onPanel, onPresence }: Props) {
  const qc = useQueryClient();
  const onNotice = useCallback(
    (notice: Notice) => {
      const key = notice === "comments-changed" ? keys.comments : keys.versions;
      qc.invalidateQueries({ queryKey: key(page.workspace_id, page.id) });
    },
    [qc, page.workspace_id, page.id],
  );
  const collab = useCollabSession(page.id, onNotice);

  useEffect(() => {
    onPresence(collab.session ? <Presence session={collab.session} /> : null);
    return () => onPresence(null);
  }, [collab.session, onPresence]);

  if (!collab.session) return null;
  return (
    <>
      {STATUS_TEXT[collab.status] && (
        <div className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200" role="status">
          {STATUS_TEXT[collab.status]}
        </div>
      )}
      <CollabEditor
        key={page.id}
        page={page}
        session={collab.session}
        synced={collab.synced}
        readOnly={collab.readOnly}
        panel={panel}
        onPanel={onPanel}
      />
    </>
  );
}

function CollabEditor({
  page,
  session,
  synced,
  readOnly,
  panel,
  onPanel,
}: {
  page: Page;
  session: CollabSession;
  synced: boolean;
  readOnly: boolean;
  panel: Panel;
  onPanel: (panel: Panel) => void;
}) {
  const me = useSession()!.user;
  const qc = useQueryClient();
  const files = { workspaceId: page.workspace_id, pageId: page.id };
  const canEdit = atLeast(page.my_role, "editor") && !readOnly;
  const canComment = atLeast(page.my_role, "commenter");

  const comments = useComments(page.workspace_id, page.id);
  const [activeThread, setActiveThread] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // The highlight plugin reads these through refs, so it always sees the latest values.
  const threadsRef = useRef<Comment[]>([]);
  threadsRef.current = (comments.data ?? []).filter((c) => c.thread_id === null);
  const activeRef = useRef(activeThread);
  activeRef.current = activeThread;
  const editorRef = useRef<Editor | null>(null);

  const selectThread = useCallback(
    (id: string | null) => {
      setActiveThread(id);
      if (id) onPanel("comments");
    },
    [onPanel],
  );

  const insertFiles = async (list: File[], at?: number) => {
    const editor = editorRef.current;
    if (!editor || !list.length) return;
    setUploadError(null);
    for (const file of list) {
      try {
        const stored = await uploadFile(page.workspace_id, page.id, file);
        const node = stored.content_type.startsWith("image/") && stored.content_type !== "image/svg+xml"
          ? { type: "image", attrs: { fileId: stored.id, alt: stored.filename } }
          : { type: "fileAttachment", attrs: { fileId: stored.id, filename: stored.filename, size: stored.size_bytes } };
        const chain = editor.chain().focus();
        (at != null ? chain.insertContentAt(at, node) : chain.insertContent(node)).run();
      } catch (e) {
        setUploadError(`${file.name}: ${e instanceof Error ? e.message : "upload failed"}`);
      }
    }
  };
  const insertFilesRef = useRef(insertFiles);
  insertFilesRef.current = insertFiles;

  const editor = useEditor(
    {
      extensions: [
        ...baseExtensions(files, {
          collaborative: true,
          pages: () => qc.getQueryData<Page[]>(keys.pages(page.workspace_id)) ?? [],
        }),
        Collaboration.configure({ document: session.doc }),
        CollaborationCaret.configure({
          provider: session.provider,
          user: { id: me.id, name: me.display_name, color: colorFor(me.id) },
        }),
        CommentHighlights.configure({
          threads: () => threadsRef.current,
          activeThread: () => activeRef.current,
          onSelectThread: (id) => selectThread(id),
        }),
      ],
      editable: false,
      editorProps: {
        attributes: { class: "ymer-prose min-h-[50vh] focus:outline-none", "aria-label": "Document" },
        handleDrop: (view, event, _slice, moved) => {
          const dropped = Array.from(event.dataTransfer?.files ?? []);
          if (moved || !dropped.length || !view.editable) return false;
          event.preventDefault();
          const pos = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos;
          void insertFilesRef.current(dropped, pos);
          return true;
        },
        handlePaste: (view, event) => {
          const pasted = Array.from(event.clipboardData?.files ?? []);
          if (!pasted.length || !view.editable) return false;
          event.preventDefault();
          void insertFilesRef.current(pasted);
          return true;
        },
      },
    },
    [session],
  );
  editorRef.current = editor;

  // Only allow typing once the stored document has arrived, and only with write access.
  useEffect(() => {
    editor?.setEditable(canEdit && synced);
  }, [editor, canEdit, synced]);

  useEffect(() => refreshHighlights(editor), [editor, comments.data, activeThread]);

  const startComment = () => {
    if (!editor) return;
    const anchored = anchorFromSelection(editor);
    if (!anchored) return;
    setDraft(anchored);
    setActiveThread(null);
    onPanel("comments");
  };

  // Jump to the anchored text when a thread is picked in the panel.
  useEffect(() => {
    if (!editor || !activeThread) return;
    const thread = threadsRef.current.find((t) => t.id === activeThread);
    const range = thread?.anchor ? resolveAnchor(editor, thread.anchor) : null;
    if (range) {
      const dom = editor.view.domAtPos(range.from).node as HTMLElement;
      (dom.nodeType === 1 ? dom : dom.parentElement)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [editor, activeThread]);

  if (!editor) return null;
  return (
    <div className="flex gap-6">
      <div className="min-w-0 flex-1">
        <Toolbar
          editor={editor}
          canEdit={canEdit}
          canComment={canComment}
          onComment={startComment}
          onFiles={(list) => void insertFiles(list)}
        />
        {uploadError && (
          <p className="mt-2 text-sm text-rose-600" role="alert">
            {uploadError}
          </p>
        )}
        <div className="pt-4">
          {!synced && <p className="text-sm text-zinc-400">Loading document…</p>}
          <EditorContent editor={editor} />
        </div>
      </div>
      {panel && (
        <aside className="fixed inset-x-0 top-14 bottom-0 z-30 overflow-hidden bg-white lg:sticky lg:top-0 lg:z-auto lg:h-[calc(100vh-7.5rem)] lg:w-80 lg:shrink-0 lg:rounded-lg lg:border lg:border-zinc-200 dark:bg-zinc-950 lg:dark:border-zinc-800">
          {panel === "comments" ? (
            <CommentsPanel
              workspaceId={page.workspace_id}
              pageId={page.id}
              myId={me.id}
              canComment={canComment}
              canModerate={atLeast(page.my_role, "editor")}
              draft={draft}
              onDraftDone={() => setDraft(null)}
              activeThread={activeThread}
              onSelectThread={selectThread}
              onChanged={() => notify(session, "comments-changed")}
            />
          ) : (
            <VersionsPanel
              workspaceId={page.workspace_id}
              pageId={page.id}
              canEdit={canEdit}
              liveState={() => bytesToBase64(Y.encodeStateAsUpdate(session.doc))}
              onRestore={(content) => editor.commands.setContent(content)}
              onChanged={() => notify(session, "versions-changed")}
            />
          )}
        </aside>
      )}
    </div>
  );
}
