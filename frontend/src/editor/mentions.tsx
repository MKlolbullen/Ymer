/**
 * Page mentions: type "@" in a document to link another page.
 *
 * The node is stored as `pageMention` with the target page id (and its title at the time,
 * as a fallback label). The backend reads these from the Yjs state on every save to build
 * backlinks (backend/ymer/content.py), so the node name and the `id` attribute are part
 * of the storage format.
 */
import Mention, { type MentionOptions } from "@tiptap/extension-mention";
import { NodeViewWrapper, ReactNodeViewRenderer, ReactRenderer, type NodeViewProps } from "@tiptap/react";
import type { SuggestionKeyDownProps, SuggestionProps } from "@tiptap/suggestion";
import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { Link } from "react-router";

import { PageIcon } from "@/components/icons";
import { usePages } from "@/lib/queries";
import type { Page } from "@/lib/types";

export type MentionContext = {
  workspaceId: string;
  /** The pages the user can see (from the sidebar's query), read when needed. */
  pages: () => Page[];
};


/** Pages matching what was typed after "@", best matches first. */
export function matchPages(pages: Page[], query: string, exclude?: string): Page[] {
  const q = query.trim().toLowerCase();
  return pages
    .filter((p) => p.id !== exclude && (p.title || "Untitled").toLowerCase().includes(q))
    .sort((a, b) => {
      const at = (a.title || "").toLowerCase().startsWith(q) ? 0 : 1;
      const bt = (b.title || "").toLowerCase().startsWith(q) ? 0 : 1;
      return at - bt || (a.title || "").localeCompare(b.title || "");
    })
    .slice(0, 8);
}

function MentionView({ node, extension }: NodeViewProps) {
  const { workspaceId } = extension.options.context as MentionContext;
  const pages = usePages(workspaceId); // re-renders when a title changes
  const id = node.attrs.id as string;
  const page = pages.data?.find((p) => p.id === id);
  const label = page ? page.title || "Untitled" : (node.attrs.label as string) || "Page";
  return (
    <NodeViewWrapper as="span" className="page-mention" data-page-id={id}>
      {page ? (
        <Link to={`/w/${workspaceId}/p/${id}`} contentEditable={false} className="page-mention-link">
          <PageIcon type={page.type} size={14} className="mr-0.5 inline-block align-[-2px]" />{label}
        </Link>
      ) : (
        <span contentEditable={false} className="page-mention-missing" title="This page was deleted or you cannot see it">
          {label}
        </span>
      )}
    </NodeViewWrapper>
  );
}

type ListHandle = { onKeyDown: (props: SuggestionKeyDownProps) => boolean };

const MentionList = forwardRef<ListHandle, SuggestionProps<Page>>(function MentionList({ items, command }, ref) {
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [items]);
  const pick = (i: number) => {
    const page = items[i];
    if (page) command({ id: page.id, label: page.title || "Untitled" } as unknown as Page);
  };
  useImperativeHandle(ref, () => ({
    onKeyDown: ({ event }) => {
      if (event.key === "ArrowDown") {
        setIndex((i) => (i + 1) % Math.max(items.length, 1));
        return true;
      }
      if (event.key === "ArrowUp") {
        setIndex((i) => (i - 1 + items.length) % Math.max(items.length, 1));
        return true;
      }
      if (event.key === "Enter") {
        pick(index);
        return true;
      }
      return false;
    },
  }));
  return (
    <div role="listbox" aria-label="Link a page" className="w-64 rounded-lg border border-zinc-200 bg-white p-1 text-sm shadow-lg dark:border-zinc-700 dark:bg-zinc-900">
      {items.length ? (
        items.map((page, i) => (
          <button
            key={page.id}
            type="button"
            role="option"
            aria-selected={i === index}
            onMouseDown={(e) => {
              e.preventDefault();
              pick(i);
            }}
            className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left ${i === index ? "bg-zinc-100 dark:bg-zinc-800" : ""}`}
          >
            <PageIcon type={page.type} className="text-zinc-500" />
            <span className="truncate">{page.title || "Untitled"}</span>
          </button>
        ))
      ) : (
        <p className="px-2 py-1 text-zinc-500">No matching pages</p>
      )}
    </div>
  );
});

export const PageMention = Mention.extend<MentionOptions & { context: MentionContext }>({
  name: "pageMention",
  addOptions() {
    return { ...this.parent!(), context: { workspaceId: "", pages: () => [] } };
  },
  addNodeView() {
    return ReactNodeViewRenderer(MentionView, { as: "span" });
  },
});

/** The mention extension for a workspace, with the "@" page picker. */
export function pageMention(context: MentionContext, currentPageId?: string) {
  return PageMention.configure({
    context,
    HTMLAttributes: { class: "page-mention" },
    renderText: ({ node }) => `@${node.attrs.label ?? ""}`,
    suggestion: {
      char: "@",
      allowSpaces: true,
      items: ({ query }: { query: string }) => matchPages(context.pages(), query, currentPageId),
      render: () => {
        let renderer: ReactRenderer<ListHandle, SuggestionProps<Page>> | null = null;
        let host: HTMLDivElement | null = null;
        const place = (props: SuggestionProps<Page>) => {
          const rect = props.clientRect?.();
          if (!rect || !host) return;
          host.style.top = `${rect.bottom + 4}px`;
          host.style.left = `${Math.min(rect.left, window.innerWidth - 272)}px`;
        };
        return {
          onStart: (props: SuggestionProps<Page>) => {
            renderer = new ReactRenderer(MentionList, { props, editor: props.editor });
            host = document.createElement("div");
            host.style.position = "fixed";
            host.style.zIndex = "60";
            host.appendChild(renderer.element);
            document.body.appendChild(host);
            place(props);
          },
          onUpdate: (props: SuggestionProps<Page>) => {
            renderer?.updateProps(props);
            place(props);
          },
          onKeyDown: (props: SuggestionKeyDownProps) => {
            if (props.event.key === "Escape") {
              host?.remove();
              return true;
            }
            return renderer?.ref?.onKeyDown(props) ?? false;
          },
          onExit: () => {
            host?.remove();
            renderer?.destroy();
            host = null;
            renderer = null;
          },
        };
      },
    },
  });
}
