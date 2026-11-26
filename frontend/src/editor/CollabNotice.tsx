import { useOnline } from "@/offline/connectivity";

import { collabNotice, type CollabState } from "./collab";

/** Connection banner shown above documents, mind maps and drawings. */
export function CollabNotice({ collab }: { collab: CollabState }) {
  const online = useOnline();
  const text = collabNotice(collab, online);
  if (!text) return null;
  return (
    <div className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200" role="status">
      {text}
    </div>
  );
}
