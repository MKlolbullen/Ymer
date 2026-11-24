import { Link } from "react-router";

import { useBacklinks } from "@/lib/queries";

/** "Linked from": pages whose documents mention this one (and that I can see). */
export function Backlinks({ workspaceId, pageId }: { workspaceId: string; pageId: string }) {
  const backlinks = useBacklinks(workspaceId, pageId);
  if (!backlinks.data?.length) return null;
  return (
    <section aria-label="Backlinks" className="mt-12 border-t border-zinc-200 pt-4 dark:border-zinc-800">
      <h2 className="mb-2 text-xs font-medium tracking-wide text-zinc-500 uppercase">
        Linked from {backlinks.data.length} {backlinks.data.length === 1 ? "page" : "pages"}
      </h2>
      <ul className="flex flex-wrap gap-2">
        {backlinks.data.map((p) => (
          <li key={p.id}>
            <Link
              to={`/w/${workspaceId}/p/${p.id}`}
              className="inline-block rounded-md border border-zinc-200 px-2 py-1 text-sm hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
            >
              {p.title || "Untitled"}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
