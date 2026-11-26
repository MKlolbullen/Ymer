import { useState, type FormEvent } from "react";

import { timeAgo } from "@/lib/format";
import { useCommentMutations, useComments, type NewComment } from "@/lib/queries";
import type { Comment } from "@/lib/types";

export type Draft = Pick<NewComment, "anchor" | "quote">;

type Props = {
  workspaceId: string;
  pageId: string;
  myId: string;
  canComment: boolean;
  canModerate: boolean;
  draft: Draft | null;
  onDraftDone: () => void;
  activeThread: string | null;
  onSelectThread: (id: string | null) => void;
  onChanged: () => void;
};

function Composer({
  placeholder,
  submitLabel,
  onSubmit,
  onCancel,
  autoFocus,
}: {
  placeholder: string;
  submitLabel: string;
  onSubmit: (body: string) => Promise<unknown>;
  onCancel?: () => void;
  autoFocus?: boolean;
}) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    try {
      await onSubmit(body.trim());
      setBody("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-2">
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e);
        }}
        placeholder={placeholder}
        autoFocus={autoFocus}
        rows={2}
        className="w-full resize-y rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm focus:border-brand-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
      />
      <div className="flex justify-end gap-2">
        {onCancel && (
          <button type="button" onClick={onCancel} className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-800">
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={busy || !body.trim()}
          className="rounded-md bg-brand-600 px-3 py-1 text-xs font-medium text-on-brand hover:bg-brand-700 disabled:opacity-50"
        >
          {submitLabel}
        </button>
      </div>
    </form>
  );
}

function CommentBody({ comment }: { comment: Comment }) {
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-medium">{comment.author?.display_name ?? "Deleted user"}</span>
        <span className="text-xs text-zinc-500" title={comment.created_at}>
          {timeAgo(comment.created_at)}
        </span>
      </div>
      <p className="mt-0.5 text-sm whitespace-pre-wrap break-words">{comment.body}</p>
    </div>
  );
}

export function CommentsPanel(props: Props) {
  const { workspaceId, pageId, myId, canComment, canModerate, draft, activeThread } = props;
  const comments = useComments(workspaceId, pageId);
  const { create, update, remove } = useCommentMutations(workspaceId, pageId, props.onChanged);
  const [showResolved, setShowResolved] = useState(false);

  const all = comments.data ?? [];
  const threads = all.filter((c) => c.thread_id === null);
  const replies = (id: string) => all.filter((c) => c.thread_id === id);
  const open = threads.filter((t) => !t.resolved_at);
  const resolved = threads.filter((t) => t.resolved_at);
  const shown = showResolved ? [...open, ...resolved] : open;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <h2 className="text-sm font-semibold">Comments</h2>
        {resolved.length > 0 && (
          <button className="text-xs text-zinc-500 hover:text-zinc-800" onClick={() => setShowResolved((v) => !v)}>
            {showResolved ? "Hide resolved" : `Show resolved (${resolved.length})`}
          </button>
        )}
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {draft && (
          <div className="rounded-lg border border-brand-500 p-3">
            {draft.quote && (
              <blockquote className="mb-2 border-l-2 border-amber-400 pl-2 text-xs text-zinc-500">{draft.quote}</blockquote>
            )}
            <Composer
              autoFocus
              placeholder="Add a comment…"
              submitLabel="Comment"
              onCancel={props.onDraftDone}
              onSubmit={async (body) => {
                const comment = await create.mutateAsync({ body, ...draft });
                props.onDraftDone();
                props.onSelectThread(comment.id);
              }}
            />
          </div>
        )}
        {canComment && !draft && (
          <Composer placeholder="Comment on the page…" submitLabel="Comment" onSubmit={(body) => create.mutateAsync({ body })} />
        )}
        {comments.isLoading && <p className="text-sm text-zinc-500">Loading…</p>}
        {!comments.isLoading && shown.length === 0 && !draft && (
          <p className="text-sm text-zinc-500">
            No comments yet.{canComment && " Select text and press Comment to start a thread."}
          </p>
        )}
        {shown.map((thread) => (
          <article
            key={thread.id}
            onClick={() => props.onSelectThread(thread.id)}
            className={`space-y-2 rounded-lg border p-3 ${
              thread.id === activeThread ? "border-amber-400 shadow-sm" : "border-zinc-200 dark:border-zinc-800"
            } ${thread.resolved_at ? "opacity-60" : ""}`}
          >
            {thread.quote && (
              <blockquote className="border-l-2 border-amber-400 pl-2 text-xs text-zinc-500">{thread.quote}</blockquote>
            )}
            <CommentBody comment={thread} />
            {replies(thread.id).map((reply) => (
              <div key={reply.id} className="group border-l border-zinc-200 pl-3 dark:border-zinc-700">
                <CommentBody comment={reply} />
                {(reply.author?.id === myId || canModerate) && (
                  <button
                    className="hidden text-xs text-zinc-400 group-hover:inline pointer-coarse:inline hover:text-rose-600"
                    onClick={() => remove.mutate(reply.id)}
                  >
                    Delete
                  </button>
                )}
              </div>
            ))}
            <div className="flex gap-3 text-xs">
              {canComment && (
                <button
                  className="text-zinc-500 hover:text-zinc-900"
                  onClick={(e) => {
                    e.stopPropagation();
                    update.mutate({ id: thread.id, resolved: !thread.resolved_at });
                  }}
                >
                  {thread.resolved_at ? "Reopen" : "Resolve"}
                </button>
              )}
              {(thread.author?.id === myId || canModerate) && (
                <button
                  className="text-zinc-500 hover:text-rose-600"
                  onClick={(e) => {
                    e.stopPropagation();
                    remove.mutate(thread.id);
                  }}
                >
                  Delete thread
                </button>
              )}
              {thread.resolved_by && (
                <span className="text-zinc-400">Resolved by {thread.resolved_by.display_name}</span>
              )}
            </div>
            {canComment && thread.id === activeThread && !thread.resolved_at && (
              <Composer
                placeholder="Reply…"
                submitLabel="Reply"
                onSubmit={(body) => create.mutateAsync({ body, thread_id: thread.id })}
              />
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
