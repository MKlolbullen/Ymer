import { useRef, useState } from "react";
import { useNavigate } from "react-router";

import { MenuItem, Popover } from "@/components/Popover";
import { ApiError } from "@/lib/api";
import { atLeast, type Page } from "@/lib/types";

import { useAiStatus, useGenerate, type GenerateBody } from "./api";
import { DemoNote } from "./AssistMenu";

/**
 * "✨ Generate" in a page's header: a mind map or a database made from this page's
 * notes (or from pasted text), created inside this page so it sits next to its source.
 */
export function GenerateMenu({ page }: { page: Page }) {
  const status = useAiStatus();
  const navigate = useNavigate();
  const generate = useGenerate(page.workspace_id);
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [paste, setPaste] = useState<null | GenerateBody["kind"]>(null);
  const [text, setText] = useState("");

  if (!status.data?.enabled || !atLeast(page.my_role, "editor") || page.type === "folder" || page.type === "factory") return null;

  const run = (body: Omit<GenerateBody, "parent_id">) =>
    generate.mutate(
      { ...body, parent_id: page.id },
      {
        onSuccess: ({ page: created }) => {
          setOpen(false);
          navigate(`/w/${page.workspace_id}/p/${created.id}`);
        },
      },
    );

  return (
    <>
      <button
        ref={button}
        onClick={() => {
          setOpen((v) => !v);
          setPaste(null);
          generate.reset();
        }}
        aria-expanded={open}
        className="rounded-md px-2.5 py-1 text-sm text-brand-600 hover:bg-zinc-100 dark:text-brand-500 dark:hover:bg-zinc-800"
      >
        ✨ Generate
      </button>
      {open && (
        <Popover anchor={button.current} onClose={() => setOpen(false)} width={340} label="Generate with AI">
          {generate.isPending ? (
            <p className="p-3 text-zinc-500" aria-live="polite">
              Reading the notes and building it… this can take a little while.
            </p>
          ) : paste ? (
            <form
              className="space-y-2 p-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (text.trim()) run({ kind: paste, text });
              }}
            >
              <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-300" htmlFor="ai-notes">
                Paste notes for a new {paste === "mindmap" ? "mind map" : "database"}
              </label>
              <textarea
                id="ai-notes"
                autoFocus
                rows={7}
                value={text}
                onChange={(e) => setText(e.target.value)}
                maxLength={60000}
                className="w-full rounded-md border border-zinc-300 bg-white p-2 text-sm focus:border-brand-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950"
              />
              <div className="flex gap-2">
                <button type="submit" disabled={!text.trim()} className="rounded-md bg-brand-600 px-3 py-1 font-medium text-on-brand disabled:opacity-50">
                  Generate
                </button>
                <button type="button" onClick={() => setPaste(null)} className="px-2 text-zinc-500">
                  Back
                </button>
              </div>
            </form>
          ) : (
            <div className="p-1">
              <p className="px-2 pt-1 pb-2 text-xs text-zinc-500">Creates a new page inside “{page.title || "Untitled"}”.</p>
              {page.type !== "mindmap" && (
                <MenuItem onClick={() => run({ kind: "mindmap", source_page_id: page.id })}>
                  <span aria-hidden>🧠</span> Mind map from this page
                </MenuItem>
              )}
              {page.type !== "database" && (
                <MenuItem onClick={() => run({ kind: "database", source_page_id: page.id })}>
                  <span aria-hidden>🗃️</span> Database from this page
                </MenuItem>
              )}
              <div className="my-1 border-t border-zinc-100 dark:border-zinc-800" />
              <MenuItem onClick={() => setPaste("mindmap")}>
                <span aria-hidden>📋</span> Mind map from pasted notes…
              </MenuItem>
              <MenuItem onClick={() => setPaste("database")}>
                <span aria-hidden>📋</span> Database from pasted notes…
              </MenuItem>
              {status.data.demo && <DemoNote className="px-2 pt-2 pb-1" />}
            </div>
          )}
          {generate.isError && (
            <p className="px-3 pb-2 text-rose-600" role="alert">
              {generate.error instanceof ApiError ? generate.error.message : "Generating failed."}
            </p>
          )}
        </Popover>
      )}
    </>
  );
}
