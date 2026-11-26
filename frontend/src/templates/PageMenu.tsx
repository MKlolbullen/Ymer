import { useRef, useState } from "react";
import { Copy, Ellipsis, LayoutTemplate } from "lucide-react";
import { useNavigate } from "react-router";

import { Icon } from "@/components/icons";
import { MenuItem, Popover } from "@/components/Popover";
import { atLeast, type Page } from "@/lib/types";

import { useDuplicatePage, useSetTemplate } from "./api";

/** The "…" menu on a page: duplicate it, or save it (with its sub-pages) as a template. */
export function PageMenu({ page }: { page: Page }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const duplicate = useDuplicatePage(page.workspace_id);
  const setTemplate = useSetTemplate(page.workspace_id, page.id);
  if (!atLeast(page.my_role, "editor")) return null;

  const run = async (action: () => Promise<void>) => {
    setError(null);
    try {
      await action();
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
    }
  };

  return (
    <>
      <button
        ref={ref}
        aria-label="More actions"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center rounded-md px-2 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        <Icon icon={Ellipsis} />
      </button>
      {open && (
        <Popover anchor={ref.current} onClose={() => setOpen(false)} width={240} label="More actions">
          <MenuItem
            disabled={duplicate.isPending}
            onClick={() =>
              void run(async () => {
                const { page: copy } = await duplicate.mutateAsync(page.id);
                navigate(`/w/${page.workspace_id}/p/${copy.id}`);
              })
            }
          >
            <Icon icon={Copy} className="text-zinc-500" /> Duplicate
          </MenuItem>
          <MenuItem disabled={setTemplate.isPending} onClick={() => void run(() => setTemplate.mutateAsync(!page.is_template).then(() => undefined))}>
            <Icon icon={LayoutTemplate} className="text-zinc-500" /> {page.is_template ? "Stop using as a template" : "Save as template"}
          </MenuItem>
          {error && <p className="px-2 py-1 text-xs text-rose-600">{error}</p>}
        </Popover>
      )}
    </>
  );
}
