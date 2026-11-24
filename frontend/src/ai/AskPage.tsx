import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { useWorkspaceLayout } from "@/pages/workspace";
import { ApiError } from "@/lib/api";

import {
  splitCitations,
  useAiStatus,
  useAsk,
  useConversation,
  useConversations,
  useDeleteConversation,
  type AiMessage,
  type Source,
} from "./api";
import { DemoNote } from "./AssistMenu";

const ICON: Record<string, string> = {
  document: "📄",
  database: "🗃️",
  mindmap: "🧠",
  drawing: "✏️",
  folder: "📁",
  row: "▤",
};

function sourcePath(workspaceId: string, s: Source) {
  const base = `/w/${workspaceId}/p/${s.page_id}`;
  return s.row_id ? `${base}?row=${s.row_id}` : base;
}

function Cited({ text, sources, workspaceId }: { text: string; sources: Source[]; workspaceId: string }) {
  return (
    <>
      {splitCitations(text).map((part, i) => {
        if (typeof part === "string") return <span key={i}>{part}</span>;
        const source = sources.find((s) => s.n === part);
        if (!source) return <span key={i}>[{part}]</span>;
        return (
          <Link
            key={i}
            to={sourcePath(workspaceId, source)}
            title={source.title}
            className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-brand-50 px-1 align-super text-[0.65rem] font-semibold text-brand-700 no-underline hover:bg-brand-500 hover:text-white dark:bg-zinc-800 dark:text-brand-500"
          >
            {part}
          </Link>
        );
      })}
    </>
  );
}

/** An answer: paragraphs and "- " lists, with [n] citations linked to their sources. */
function Answer({ message, workspaceId }: { message: AiMessage; workspaceId: string }) {
  const blocks: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (!items.length) return;
    blocks.push(
      <ul key={blocks.length} className="list-disc space-y-1 pl-5">
        {items.map((item, i) => (
          <li key={i}>
            <Cited text={item} sources={message.sources} workspaceId={workspaceId} />
          </li>
        ))}
      </ul>,
    );
    items = [];
  };
  for (const line of message.content.split("\n")) {
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (bullet) items.push(bullet[1] ?? "");
    else {
      flush();
      if (line.trim())
        blocks.push(
          <p key={blocks.length}>
            <Cited text={line} sources={message.sources} workspaceId={workspaceId} />
          </p>,
        );
    }
  }
  flush();
  const cited = new Set(splitCitations(message.content).filter((p) => typeof p === "number"));
  const shown = message.sources.filter((s) => cited.has(s.n));
  return (
    <div className="space-y-3">
      <div className="space-y-2 leading-7">{blocks}</div>
      {shown.length > 0 && (
        <ol className="grid gap-2 sm:grid-cols-2" aria-label="Sources">
          {shown.map((s) => (
            <li key={s.n}>
              <Link
                to={sourcePath(workspaceId, s)}
                className="flex h-full flex-col gap-1 rounded-lg border border-zinc-200 p-2.5 text-sm hover:border-brand-500 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
              >
                <span className="flex items-center gap-1.5 font-medium">
                  <span className="rounded bg-zinc-100 px-1 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{s.n}</span>
                  <span aria-hidden>{ICON[s.type] ?? "📄"}</span>
                  <span className="truncate">{s.title || "Untitled"}</span>
                </span>
                <span className="line-clamp-2 text-xs text-zinc-500">{s.snippet}</span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** Workspace Q&A: ask questions, get answers drawn from pages you can see. */
export function AskPage() {
  const { workspace, setHeader } = useWorkspaceLayout();
  const { conversationId = null } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const status = useAiStatus();
  const list = useConversations(workspace.id);
  const thread = useConversation(workspace.id, conversationId);
  const ask = useAsk(workspace.id);
  const { mutate: remove } = useDeleteConversation(workspace.id);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const asked = useRef(false);

  useEffect(() => {
    setHeader(<h1 className="text-sm font-medium">✨ Ask AI</h1>);
    return () => setHeader(null);
  }, [setHeader]);

  const send = (text: string) => {
    const q = text.trim();
    if (!q || ask.isPending) return;
    setPending(q);
    setQuestion("");
    ask.mutate(
      { question: q, conversation_id: conversationId },
      {
        onSuccess: (result) => {
          if (result.conversation.id !== conversationId) navigate(`/w/${workspace.id}/ask/${result.conversation.id}`);
        },
        onSettled: () => setPending(null),
      },
    );
  };

  // "Ask AI" from the search dialog arrives as ?q=.
  useEffect(() => {
    const q = params.get("q");
    if (q && !asked.current && status.data?.enabled) {
      asked.current = true;
      setParams({}, { replace: true });
      send(q);
    }
  });

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [thread.data, pending]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(question);
  };

  if (status.data && !status.data.enabled) {
    return <p className="text-sm text-zinc-500">AI is turned off on this server.</p>;
  }

  const messages = conversationId ? (thread.data?.messages ?? []) : [];
  return (
    <div className="mx-auto flex h-full max-w-6xl gap-6">
      <aside className="hidden w-60 shrink-0 flex-col gap-1 lg:flex" aria-label="Conversations">
        <button
          onClick={() => navigate(`/w/${workspace.id}/ask`)}
          className="mb-2 rounded-md border border-zinc-300 px-3 py-1.5 text-left text-sm font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
        >
          + New question
        </button>
        {(list.data ?? []).map((c) => (
          <div key={c.id} className="group flex items-center">
            <Link
              to={`/w/${workspace.id}/ask/${c.id}`}
              className={`min-w-0 flex-1 truncate rounded-md px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-900 ${
                c.id === conversationId ? "bg-zinc-100 font-medium dark:bg-zinc-900" : "text-zinc-600 dark:text-zinc-400"
              }`}
            >
              {c.title || "Untitled"}
            </Link>
            <button
              aria-label={`Delete “${c.title}”`}
              onClick={() => {
                if (!window.confirm("Delete this conversation?")) return;
                remove(c.id);
                if (c.id === conversationId) navigate(`/w/${workspace.id}/ask`);
              }}
              className="invisible px-1.5 text-zinc-400 group-hover:visible hover:text-rose-600"
            >
              ×
            </button>
          </div>
        ))}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 space-y-6 pb-4">
          {!messages.length && !pending && (
            <div className="mx-auto max-w-xl pt-10 text-center">
              <p className="text-3xl" aria-hidden>
                ✨
              </p>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">Ask your workspace</h2>
              <p className="mt-2 text-sm text-zinc-500">
                Answers are drawn only from pages and database rows you can open, with links to where each fact comes
                from. Your conversations are private to you.
              </p>
            </div>
          )}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <p className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-sm text-white">{m.content}</p>
              </div>
            ) : (
              <div key={m.id} className="max-w-3xl text-sm">
                <Answer message={m} workspaceId={workspace.id} />
              </div>
            ),
          )}
          {pending && (
            <>
              <div className="flex justify-end">
                <p className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-sm text-white">{pending}</p>
              </div>
              <p className="text-sm text-zinc-500">Reading your pages…</p>
            </>
          )}
          {ask.isError && (
            <p className="text-sm text-rose-600" role="alert">
              {ask.error instanceof ApiError ? ask.error.message : "The question could not be answered."}
            </p>
          )}
          <div ref={bottom} />
        </div>
        <form onSubmit={submit} className="sticky bottom-0 bg-white pt-2 pb-1 dark:bg-zinc-950">
          {status.data?.demo && <DemoNote className="mb-1.5" />}
          <div className="flex gap-2 rounded-xl border border-zinc-300 bg-white p-1.5 shadow-sm focus-within:border-brand-500 dark:border-zinc-700 dark:bg-zinc-900">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(question);
                }
              }}
              rows={1}
              maxLength={2000}
              placeholder={conversationId ? "Ask a follow-up…" : "Ask anything about your pages…"}
              aria-label="Question"
              className="max-h-40 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm focus:outline-none"
            />
            <button
              type="submit"
              disabled={!question.trim() || ask.isPending}
              className="self-end rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              Ask
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
