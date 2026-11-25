/** The AI layer's API: status, writing assist, semantic search, Q&A and generation. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useSession } from "@/lib/auth";
import { api } from "@/lib/api";
import { keys as baseKeys } from "@/lib/queries";
import type { Page, SearchResult } from "@/lib/types";

export type AiStatus = {
  enabled: boolean;
  semantic_search: boolean;
  provider: string | null;
  model: string | null;
  embedding_model: string | null;
  /** True when the offline stand-in model answers: its output is a demo, not real AI. */
  demo: boolean;
};

export type AssistAction = "improve" | "fix" | "shorten" | "expand" | "summarize" | "bullets" | "custom";

export type Source = {
  n: number;
  page_id: string;
  row_id: string | null;
  type: string;
  title: string;
  snippet: string;
};

export type AiMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  created_at: string;
};

export type Conversation = { id: string; title: string; created_at: string; updated_at: string };

const ws = (id: string) => `/workspaces/${id}`;

export const aiKeys = {
  status: ["ai-status"] as const,
  semantic: (w: string, q: string) => ["ai-search", w, q] as const,
  conversations: (w: string) => ["ai-conversations", w] as const,
  conversation: (w: string, c: string) => ["ai-conversations", w, c] as const,
};

export function useAiStatus() {
  const session = useSession();
  return useQuery({
    queryKey: [...aiKeys.status, session?.user.id],
    queryFn: () => api<AiStatus>("/ai/status"),
    staleTime: 5 * 60_000,
  });
}

export function assist(
  workspaceId: string,
  pageId: string,
  body: { action: AssistAction; text?: string; instruction?: string },
) {
  return api<{ text: string; demo: boolean }>(`${ws(workspaceId)}/pages/${pageId}/ai/assist`, {
    method: "POST",
    json: body,
  });
}

export function useSemanticSearch(workspaceId: string, q: string, enabled: boolean) {
  return useQuery({
    queryKey: aiKeys.semantic(workspaceId, q),
    queryFn: ({ signal }) =>
      api<{ results: SearchResult[] }>(`${ws(workspaceId)}/ai/search?q=${encodeURIComponent(q)}`, { signal }).then(
        (r) => r.results,
      ),
    enabled: enabled && q.trim().length > 1,
    staleTime: 10_000,
    placeholderData: (previous) => previous,
  });
}

export function useConversations(workspaceId: string) {
  return useQuery({
    queryKey: aiKeys.conversations(workspaceId),
    queryFn: () =>
      api<{ conversations: Conversation[] }>(`${ws(workspaceId)}/ai/conversations`).then((r) => r.conversations),
  });
}

export function useConversation(workspaceId: string, conversationId: string | null) {
  return useQuery({
    queryKey: aiKeys.conversation(workspaceId, conversationId ?? ""),
    queryFn: () =>
      api<{ conversation: Conversation; messages: AiMessage[] }>(
        `${ws(workspaceId)}/ai/conversations/${conversationId}`,
      ),
    enabled: !!conversationId,
  });
}

export type AskResult = { conversation: Conversation; question: AiMessage; answer: AiMessage; demo: boolean };

export function useAsk(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { question: string; conversation_id?: string | null }) =>
      api<AskResult>(`${ws(workspaceId)}/ai/ask`, { method: "POST", json: body }),
    onSuccess: (result) => {
      qc.setQueryData(
        aiKeys.conversation(workspaceId, result.conversation.id),
        (old: { conversation: Conversation; messages: AiMessage[] } | undefined) => ({
          conversation: result.conversation,
          messages: [...(old?.messages ?? []), result.question, result.answer],
        }),
      );
      qc.invalidateQueries({ queryKey: aiKeys.conversations(workspaceId), exact: true });
    },
  });
}

export function useDeleteConversation(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`${ws(workspaceId)}/ai/conversations/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: aiKeys.conversations(workspaceId) }),
  });
}

export type GenerateBody = {
  kind: "mindmap" | "database";
  source_page_id?: string;
  text?: string;
  parent_id?: string | null;
  title?: string;
};

export function useGenerate(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GenerateBody) =>
      api<{ page: Page; summary: Record<string, number> }>(`${ws(workspaceId)}/ai/generate`, {
        method: "POST",
        json: body,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: baseKeys.pages(workspaceId) }),
  });
}

/**
 * Model text (paragraphs separated by blank lines, "- " bullet lines) as TipTap content.
 * Kept to paragraphs and bullet lists so the result fits any document.
 */
export function textToContent(text: string): Record<string, unknown>[] {
  const nodes: Record<string, unknown>[] = [];
  const paragraph = (t: string) => ({ type: "paragraph", content: t ? [{ type: "text", text: t }] : [] });
  for (const block of text.trim().split(/\n\s*\n/)) {
    const lines = block.split("\n").map((l) => l.trimEnd()).filter((l) => l.trim());
    let items: string[] = [];
    let prose: string[] = [];
    const flush = () => {
      if (prose.length) nodes.push(paragraph(prose.join(" ")));
      if (items.length)
        nodes.push({ type: "bulletList", content: items.map((i) => ({ type: "listItem", content: [paragraph(i)] })) });
      prose = [];
      items = [];
    };
    for (const line of lines) {
      const bullet = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (bullet) {
        if (prose.length) flush();
        items.push(bullet[1] ?? "");
      } else {
        if (items.length) flush();
        prose.push(line.trim());
      }
    }
    flush();
  }
  return nodes;
}

/** Split an answer into text and citation markers like [2]. */
export function splitCitations(text: string): (string | number)[] {
  const out: (string | number)[] = [];
  let last = 0;
  for (const m of text.matchAll(/\[(\d{1,2})\]/g)) {
    if (m.index! > last) out.push(text.slice(last, m.index));
    out.push(Number(m[1]));
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
