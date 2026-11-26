import { Paperclip } from "lucide-react";
import { NodeViewWrapper, type ReactNodeViewProps } from "@tiptap/react";

import { Icon } from "@/components/icons";
import { useFile } from "@/lib/queries";

import type { FileContext } from "./extensions";

function useFileUrl(props: ReactNodeViewProps) {
  const { workspaceId, pageId } = props.extension.options as FileContext;
  return useFile(workspaceId, pageId, props.node.attrs.fileId as string | null);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileImageView(props: ReactNodeViewProps) {
  const file = useFileUrl(props);
  const { fileId, src, alt } = props.node.attrs as { fileId: string | null; src: string | null; alt: string | null };
  const url = fileId ? file.data?.url : src;
  return (
    <NodeViewWrapper
      className={`my-4 ${props.selected ? "rounded-md ring-2 ring-brand-500" : ""}`}
      data-drag-handle
    >
      {url ? (
        <img src={url} alt={alt ?? ""} className="max-w-full rounded-md" draggable={false} />
      ) : (
        <div className="flex h-32 items-center justify-center rounded-md bg-zinc-100 text-sm text-zinc-500 dark:bg-zinc-800">
          {file.isError ? "Image unavailable" : "Loading image…"}
        </div>
      )}
    </NodeViewWrapper>
  );
}

export function FileAttachmentView(props: ReactNodeViewProps) {
  const file = useFileUrl(props);
  const { filename, size } = props.node.attrs as { filename: string; size: number };
  return (
    <NodeViewWrapper className="my-3" data-drag-handle>
      <a
        href={file.data?.url}
        target="_blank"
        rel="noreferrer noopener"
        className={`flex items-center gap-3 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800 ${props.selected ? "ring-2 ring-brand-500" : ""}`}
        contentEditable={false}
      >
        <Icon icon={Paperclip} size={18} className="text-zinc-500" />
        <span className="min-w-0 flex-1 truncate font-medium">{filename}</span>
        <span className="text-xs text-zinc-500">{formatBytes(size)}</span>
      </a>
    </NodeViewWrapper>
  );
}
