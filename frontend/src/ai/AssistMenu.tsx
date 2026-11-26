import type { Editor } from "@tiptap/react";
import { AlignLeft, Expand, List, Shrink, Sparkles, SpellCheck, WandSparkles } from "lucide-react";
import { useRef, useState } from "react";

import { Icon, type LucideIcon } from "@/components/icons";
import { inputClass, MenuItem, Popover } from "@/components/Popover";
import { ApiError } from "@/lib/api";

import { assist, textToContent, useAiStatus, type AssistAction } from "./api";

type Range = { from: number; to: number; text: string };
type Request = { action: AssistAction; instruction?: string };

const ACTIONS: { action: AssistAction; label: string; icon: LucideIcon }[] = [
  { action: "improve", label: "Improve writing", icon: WandSparkles },
  { action: "fix", label: "Fix spelling and grammar", icon: SpellCheck },
  { action: "shorten", label: "Make shorter", icon: Shrink },
  { action: "expand", label: "Make longer", icon: Expand },
  { action: "summarize", label: "Summarize", icon: AlignLeft },
  { action: "bullets", label: "Turn into a bulleted list", icon: List },
];

export function DemoNote({ className = "" }: { className?: string }) {
  return (
    <p className={`text-xs text-amber-700 dark:text-amber-300 ${className}`}>
      Demo model: this server has no AI provider set up, so an offline stand-in wrote this.
    </p>
  );
}

/**
 * The toolbar's AI button. With text selected, editors can rewrite it (improve, fix,
 * shorten, expand, summarize, bullets or their own instruction) and then replace the
 * selection or insert the result below it. Without a selection, anyone can summarize
 * the whole page. Readers only see results; they cannot insert them.
 */
export function AssistMenu({
  editor,
  workspaceId,
  pageId,
  canEdit,
}: {
  editor: Editor;
  workspaceId: string;
  pageId: string;
  canEdit: boolean;
}) {
  const status = useAiStatus();
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState<Range | null>(null);
  const [instruction, setInstruction] = useState("");
  const [request, setRequest] = useState<Request | null>(null);
  const [result, setResult] = useState<{ text: string; demo: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!status.data?.enabled) return null;

  const openMenu = () => {
    const { from, to, empty } = editor.state.selection;
    setRange(empty ? null : { from, to, text: editor.state.doc.textBetween(from, to, "\n\n", " ") });
    setResult(null);
    setError(null);
    setRequest(null);
    setInstruction("");
    setOpen(true);
  };

  const run = async (req: Request) => {
    setRequest(req);
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const out = await assist(workspaceId, pageId, { ...req, text: range?.text ?? "" });
      setResult({ text: out.text, demo: out.demo });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The AI request failed.");
    } finally {
      setBusy(false);
    }
  };

  const clamp = (pos: number) => Math.max(0, Math.min(pos, editor.state.doc.content.size));
  const content = (text: string) => {
    const nodes = textToContent(text);
    // One plain paragraph replaces inline, so a rewritten sentence stays in its paragraph.
    return nodes.length === 1 && nodes[0]?.type === "paragraph" ? text.trim() : nodes;
  };

  const replace = () => {
    if (!range || !result) return;
    editor.chain().focus().insertContentAt({ from: clamp(range.from), to: clamp(range.to) }, content(result.text)).run();
    setOpen(false);
  };

  const insertBelow = () => {
    if (!result) return;
    const at = range ? clamp(range.to) : editor.state.selection.to;
    const $pos = editor.state.doc.resolve(at);
    const after = $pos.depth >= 1 ? $pos.after(1) : at;
    editor.chain().focus().insertContentAt(after, textToContent(result.text)).run();
    setOpen(false);
  };

  const label = "AI assist: rewrite the selection or summarize the page";
  return (
    <>
      <button
        ref={button}
        type="button"
        title={label}
        aria-label={label}
        aria-expanded={open}
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => (open ? setOpen(false) : openMenu())}
        className="inline-flex h-8 items-center gap-1 rounded px-2 text-sm font-medium text-brand-600 hover:bg-brand-50 dark:text-brand-500 dark:hover:bg-zinc-800"
      >
        <Icon icon={Sparkles} /><span className="hidden sm:inline">AI</span>
      </button>
      <span className="mx-1 h-5 w-px bg-zinc-200 dark:bg-zinc-700" aria-hidden />
      {open && (
        <Popover anchor={button.current} onClose={() => setOpen(false)} width={400} label="AI assist">
          {!request && (
            <div className="p-1">
              <p className="px-2 pt-1 pb-2 text-xs text-zinc-500">
                {range
                  ? `${range.text.length.toLocaleString()} characters selected`
                  : canEdit
                    ? "Select text to rewrite it, or summarize the whole page."
                    : "Summarize the whole page."}
              </p>
              {range && canEdit ? (
                <>
                  {ACTIONS.map((a) => (
                    <MenuItem key={a.action} onClick={() => void run({ action: a.action })}>
                      <Icon icon={a.icon} className="text-zinc-500" />
                      {a.label}
                    </MenuItem>
                  ))}
                  <form
                    className="mt-1 flex gap-1 border-t border-zinc-100 px-1 pt-2 dark:border-zinc-800"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (instruction.trim()) void run({ action: "custom", instruction: instruction.trim() });
                    }}
                  >
                    <input
                      value={instruction}
                      onChange={(e) => setInstruction(e.target.value)}
                      placeholder="Or tell the AI what to do…"
                      aria-label="Instruction"
                      maxLength={1000}
                      className={inputClass}
                    />
                    <button
                      type="submit"
                      disabled={!instruction.trim()}
                      className="rounded-md bg-brand-600 px-2.5 text-sm font-medium text-on-brand disabled:opacity-50"
                    >
                      Go
                    </button>
                  </form>
                </>
              ) : (
                <MenuItem onClick={() => void run({ action: "summarize" })}>
                  <Icon icon={AlignLeft} className="text-zinc-500" />
                  {range ? "Summarize the selection" : "Summarize this page"}
                </MenuItem>
              )}
            </div>
          )}
          {request && (
            <div className="space-y-2 p-2" aria-live="polite">
              {busy && <p className="text-zinc-500">Writing…</p>}
              {error && (
                <p className="text-rose-600" role="alert">
                  {error}
                </p>
              )}
              {result && (
                <>
                  <div className="max-h-72 overflow-y-auto rounded-md bg-zinc-50 p-2 leading-6 whitespace-pre-wrap dark:bg-zinc-950">
                    {result.text}
                  </div>
                  {result.demo && <DemoNote />}
                  <div className="flex flex-wrap gap-1.5">
                    {canEdit && range && (
                      <button onClick={replace} className="rounded-md bg-brand-600 px-2.5 py-1 font-medium text-on-brand hover:bg-brand-700">
                        Replace selection
                      </button>
                    )}
                    {canEdit && (
                      <button
                        onClick={insertBelow}
                        className={
                          range
                            ? "rounded-md border border-zinc-300 px-2.5 py-1 hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
                            : "rounded-md bg-brand-600 px-2.5 py-1 font-medium text-on-brand hover:bg-brand-700"
                        }
                      >
                        Insert below
                      </button>
                    )}
                    <button
                      onClick={() => void navigator.clipboard?.writeText(result.text)}
                      className="rounded-md px-2.5 py-1 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      Copy
                    </button>
                  </div>
                </>
              )}
              {!busy && (
                <div className="flex gap-1.5 border-t border-zinc-100 pt-2 text-xs dark:border-zinc-800">
                  <button onClick={() => void run(request)} className="rounded px-2 py-1 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800">
                    Try again
                  </button>
                  <button
                    onClick={() => {
                      setRequest(null);
                      setResult(null);
                      setError(null);
                    }}
                    className="rounded px-2 py-1 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  >
                    Back
                  </button>
                  <button onClick={() => setOpen(false)} className="ml-auto rounded px-2 py-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                    Discard
                  </button>
                </div>
              )}
            </div>
          )}
        </Popover>
      )}
    </>
  );
}
