/**
 * The document schema: which blocks and marks Ymer documents can contain.
 *
 * Everything that renders a document (live editor, version preview) uses
 * `baseExtensions`, so a stored document always matches the schema. Content is only
 * ever what this schema allows, which is also what keeps pasted HTML from injecting
 * markup (README: "sanitization on rich text").
 */
import { mergeAttributes, Node, type Extensions } from "@tiptap/core";
import Image, { type ImageOptions } from "@tiptap/extension-image";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { Placeholder } from "@tiptap/extensions";
import { ReactNodeViewRenderer } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

import { FileAttachmentView, FileImageView } from "./FileViews";
import { pageMention, type MentionContext } from "./mentions";

export type FileContext = { workspaceId: string; pageId: string };

/** An image stored in Ymer (by file id); `src` is only used for external images. */
export const FileImage = Image.extend<ImageOptions & FileContext>({
  addOptions() {
    return { ...this.parent!(), workspaceId: "", pageId: "" };
  },
  addAttributes() {
    return {
      ...this.parent?.(),
      fileId: {
        default: null,
        parseHTML: (el) => el.getAttribute("data-file-id"),
        renderHTML: (attrs) => (attrs.fileId ? { "data-file-id": attrs.fileId } : {}),
      },
    };
  },
  addNodeView() {
    return ReactNodeViewRenderer(FileImageView);
  },
});

/** A non-image file shown as a download card. */
export const FileAttachment = Node.create<FileContext>({
  name: "fileAttachment",
  group: "block",
  atom: true,
  draggable: true,
  addOptions() {
    return { workspaceId: "", pageId: "" };
  },
  addAttributes() {
    return {
      fileId: { default: null, parseHTML: (el) => el.getAttribute("data-file-id") },
      filename: { default: "file", parseHTML: (el) => el.getAttribute("data-filename") },
      size: { default: 0, parseHTML: (el) => Number(el.getAttribute("data-size") ?? 0) },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-file-attachment]" }];
  },
  renderHTML({ HTMLAttributes, node }) {
    return [
      "div",
      mergeAttributes(HTMLAttributes, {
        "data-file-attachment": "",
        "data-file-id": node.attrs.fileId,
        "data-filename": node.attrs.filename,
        "data-size": node.attrs.size,
      }),
      node.attrs.filename,
    ];
  },
  addNodeView() {
    return ReactNodeViewRenderer(FileAttachmentView);
  },
});

export function baseExtensions(
  files: FileContext,
  { collaborative, pages = () => [] }: { collaborative: boolean; pages?: MentionContext["pages"] },
): Extensions {
  return [
    StarterKit.configure({
      // Yjs keeps the undo history when collaborating (Collaboration adds its own).
      undoRedo: collaborative ? false : undefined,
      heading: { levels: [1, 2, 3] },
      link: { openOnClick: false, autolink: true, defaultProtocol: "https" },
    }),
    Placeholder.configure({ placeholder: "Write something, type @ to link a page, or paste an image…" }),
    TaskList,
    TaskItem.configure({ nested: true }),
    FileImage.configure(files),
    FileAttachment.configure(files),
    pageMention({ workspaceId: files.workspaceId, pages }, files.pageId),
  ];
}
