import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { useGrantMutations, useGrants } from "@/lib/queries";
import { atLeast, type Page, type Role } from "@/lib/types";

import { fieldClass } from "./Popover";

const GRANTABLE: Role[] = ["viewer", "commenter", "editor"];

function message(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.errors?.[0]?.message ?? error.message;
  return "Something went wrong.";
}

/** Share one page (and everything inside it) with people, in or outside the workspace. */
export function ShareDialog({ page }: { page: Page }) {
  const grants = useGrants(page.workspace_id, page.id);
  const { share, update, remove } = useGrantMutations(page.workspace_id, page.id);
  const roles = GRANTABLE.filter((r) => atLeast(page.my_role, r));
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    await share.mutateAsync({ email: email.trim(), role });
    setEmail("");
  };

  return (
    <div className="space-y-3 p-2">
      <div>
        <h2 className="text-sm font-semibold">Share “{page.title || "Untitled"}”</h2>
        <p className="text-xs text-zinc-500">
          Access covers this page and every page inside it. It can widen access for people outside the workspace or
          narrow it for members.
        </p>
      </div>
      <form onSubmit={submit} className="flex gap-1">
        <input
          type="email"
          required
          aria-label="Email"
          placeholder="name@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={`${fieldClass} min-w-0 flex-1`}
        />
        <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)} className={`${fieldClass} w-28 shrink-0`}>
          {roles.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <button type="submit" disabled={share.isPending} className="rounded-md bg-brand-600 px-3 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60">
          Share
        </button>
      </form>
      {share.error && <p className="text-xs text-rose-600">{message(share.error)}</p>}
      <ul aria-label="People with access" className="space-y-1">
        {grants.data?.map((g) => (
          <li key={g.user_id} className="flex items-center gap-2 text-sm">
            <span className="min-w-0 flex-1 truncate" title={g.email}>
              {g.display_name} <span className="text-xs text-zinc-500">{g.email}</span>
            </span>
            <select
              aria-label={`Role of ${g.display_name}`}
              value={g.role}
              onChange={(e) => update.mutate({ userId: g.user_id, role: e.target.value as Role })}
              className="rounded border border-zinc-300 bg-transparent px-1 py-0.5 text-xs dark:border-zinc-700"
            >
              {GRANTABLE.map((r) => (
                <option key={r} value={r} disabled={!roles.includes(r)}>
                  {r}
                </option>
              ))}
            </select>
            <button type="button" aria-label={`Remove ${g.display_name}`} onClick={() => remove.mutate(g.user_id)} className="px-1 text-zinc-400 hover:text-rose-600">
              ×
            </button>
          </li>
        ))}
        {grants.data?.length === 0 && <li className="text-xs text-zinc-500">No one has been given access to this page directly.</li>}
      </ul>
      {(update.error || remove.error) && <p className="text-xs text-rose-600">{message(update.error ?? remove.error)}</p>}
    </div>
  );
}
