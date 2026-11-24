import { useQueryClient } from "@tanstack/react-query";
import { EditorContent, useEditor, type JSONContent } from "@tiptap/react";
import { yXmlFragmentToProsemirrorJSON } from "@tiptap/y-tiptap";
import { useState } from "react";
import { createPortal } from "react-dom";
import * as Y from "yjs";

import { base64ToBytes } from "@/lib/base64";
import { formatDateTime, timeAgo } from "@/lib/format";
import { fetchVersion, keys, saveVersion, useVersions } from "@/lib/queries";
import type { Version } from "@/lib/types";

import { baseExtensions } from "./extensions";

type Props = {
  workspaceId: string;
  pageId: string;
  canEdit: boolean;
  /** The live document's Yjs state, base64. */
  liveState: () => string;
  /** Replace the live document's content (as a normal, shared edit). */
  onRestore: (content: JSONContent) => void;
  onChanged: () => void;
};

/** Decode a version's Yjs state into editor JSON. */
export function versionContent(state: string): JSONContent {
  const doc = new Y.Doc();
  Y.applyUpdate(doc, base64ToBytes(state));
  const json = yXmlFragmentToProsemirrorJSON(doc.getXmlFragment("default")) as JSONContent;
  doc.destroy();
  return json;
}

function Preview({ workspaceId, pageId, content }: { workspaceId: string; pageId: string; content: JSONContent }) {
  const editor = useEditor(
    {
      extensions: baseExtensions({ workspaceId, pageId }, { collaborative: false }),
      content,
      editable: false,
    },
    [content],
  );
  return <EditorContent editor={editor} className="ymer-prose" />;
}

export function VersionsPanel({ workspaceId, pageId, canEdit, liveState, onRestore, onChanged }: Props) {
  const qc = useQueryClient();
  const versions = useVersions(workspaceId, pageId);
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ version: Version; content: JSONContent } | null>(null);

  const refresh = () => {
    onChanged();
    return qc.invalidateQueries({ queryKey: keys.versions(workspaceId, pageId) });
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await saveVersion(workspaceId, pageId, liveState(), label.trim() || undefined);
      setLabel("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the version.");
    } finally {
      setSaving(false);
    }
  };

  const open = async (version: Version) => {
    const full = await fetchVersion(workspaceId, pageId, version.id);
    setPreview({ version: full, content: versionContent(full.state!) });
  };

  const restore = async () => {
    if (!preview) return;
    // Keep what is there now, so the restore itself can be undone from history.
    try {
      await saveVersion(workspaceId, pageId, liveState(), `Before restoring ${formatDateTime(preview.version.created_at)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the current version, so nothing was restored.");
      setPreview(null);
      return;
    }
    onRestore(preview.content);
    setPreview(null);
    await refresh();
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <h2 className="text-sm font-semibold">Version history</h2>
        <p className="mt-0.5 text-xs text-zinc-500">Saved automatically every 10 minutes while people edit.</p>
        {canEdit && (
          <div className="mt-3 flex gap-2">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Name this version (optional)"
              maxLength={200}
              className="min-w-0 flex-1 rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
            <button
              onClick={save}
              disabled={saving}
              className="rounded-md bg-brand-600 px-3 py-1 text-xs font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              Save version
            </button>
          </div>
        )}
        {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
      </div>
      <ul className="flex-1 overflow-y-auto p-2">
        {versions.data?.length === 0 && <li className="p-2 text-sm text-zinc-500">No versions yet.</li>}
        {versions.data?.map((v) => (
          <li key={v.id}>
            <button
              onClick={() => open(v)}
              className={`w-full rounded-md px-3 py-2 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                preview?.version.id === v.id ? "bg-zinc-100 dark:bg-zinc-800" : ""
              }`}
            >
              <div className="text-sm font-medium">{v.label ?? "Automatic snapshot"}</div>
              <div className="text-xs text-zinc-500" title={formatDateTime(v.created_at)}>
                {timeAgo(v.created_at)}
                {v.created_by && ` · ${v.created_by.display_name}`}
              </div>
            </button>
          </li>
        ))}
      </ul>

      {preview &&
        createPortal(
        <div
          role="dialog"
          aria-modal
          aria-label="Version preview"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={() => setPreview(null)}
        >
          <div
            className="flex max-h-full w-full max-w-3xl flex-col rounded-xl bg-white shadow-xl dark:bg-zinc-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-4 border-b border-zinc-200 px-5 py-3 dark:border-zinc-800">
              <div>
                <div className="font-semibold">{preview.version.label ?? "Automatic snapshot"}</div>
                <div className="text-xs text-zinc-500">{formatDateTime(preview.version.created_at)}</div>
              </div>
              <div className="flex gap-2">
                <button onClick={() => setPreview(null)} className="rounded-md px-3 py-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800">
                  Close
                </button>
                {canEdit && (
                  <button
                    onClick={restore}
                    className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
                  >
                    Restore this version
                  </button>
                )}
              </div>
            </div>
            <div className="overflow-y-auto px-8 py-6">
              <Preview workspaceId={workspaceId} pageId={pageId} content={preview.content} />
            </div>
          </div>
        </div>,
          document.body,
        )}
    </div>
  );
}
