/**
 * Comment anchors: a text range stored as two Yjs relative positions, so it keeps
 * pointing at the same words while people edit around it. Commenters can create one
 * without write access, since the anchor lives on the comment, not in the document.
 */
import { Extension, type Editor } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import {
  absolutePositionToRelativePosition,
  relativePositionToAbsolutePosition,
  ySyncPluginKey,
} from "@tiptap/y-tiptap";
import * as Y from "yjs";

import { base64ToBytes as fromBase64, bytesToBase64 as toBase64 } from "@/lib/base64";
import type { Comment, CommentAnchor } from "@/lib/types";


/** Anchor for the editor's current selection, or null when nothing is selected. */
export function anchorFromSelection(editor: Editor): { anchor: CommentAnchor; quote: string } | null {
  const { from, to, empty } = editor.state.selection;
  const sync = ySyncPluginKey.getState(editor.state);
  if (empty || !sync?.binding) return null;
  const rel = (pos: number) =>
    toBase64(Y.encodeRelativePosition(absolutePositionToRelativePosition(pos, sync.type, sync.binding.mapping)));
  return {
    anchor: { from: rel(from), to: rel(to) },
    quote: editor.state.doc.textBetween(from, to, " ").slice(0, 1000),
  };
}

export function resolveAnchor(editor: Editor, anchor: CommentAnchor): { from: number; to: number } | null {
  const sync = ySyncPluginKey.getState(editor.state);
  if (!sync?.binding) return null;
  try {
    const abs = (text: string) =>
      relativePositionToAbsolutePosition(sync.doc, sync.type, Y.decodeRelativePosition(fromBase64(text)), sync.binding.mapping);
    const from = abs(anchor.from);
    const to = abs(anchor.to);
    if (from == null || to == null || to <= from) return null; // the text was deleted
    return { from, to };
  } catch {
    return null;
  }
}

export const commentsKey = new PluginKey("commentHighlights");

type Options = {
  threads: () => Comment[];
  activeThread: () => string | null;
  onSelectThread: (threadId: string) => void;
};

/** Highlights the anchored text of open threads; clicking a highlight selects its thread. */
export const CommentHighlights = Extension.create<Options>({
  name: "commentHighlights",
  addOptions() {
    return { threads: () => [], activeThread: () => null, onSelectThread: () => {} };
  },
  addProseMirrorPlugins() {
    const { editor, options } = this;
    return [
      new Plugin({
        key: commentsKey,
        props: {
          decorations: (state) => {
            const decorations: Decoration[] = [];
            const active = options.activeThread();
            for (const thread of options.threads()) {
              if (!thread.anchor || thread.resolved_at) continue;
              const range = resolveAnchor(editor, thread.anchor);
              if (!range) continue;
              decorations.push(
                Decoration.inline(range.from, range.to, {
                  class: thread.id === active ? "comment-anchor comment-anchor-active" : "comment-anchor",
                  "data-comment-thread": thread.id,
                }),
              );
            }
            return DecorationSet.create(state.doc, decorations);
          },
          handleClick: (_view, _pos, event) => {
            const target = (event.target as HTMLElement).closest("[data-comment-thread]");
            const id = target?.getAttribute("data-comment-thread");
            if (id) options.onSelectThread(id);
            return false;
          },
        },
      }),
    ];
  },
});

/** Ask the editor to redraw highlights after the comment list changed. */
export function refreshHighlights(editor: Editor | null): void {
  if (editor && !editor.isDestroyed) editor.view.dispatch(editor.state.tr.setMeta(commentsKey, true));
}
