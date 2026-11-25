import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { fieldClass } from "@/components/Popover";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/auth";
import { useMemberMutations, useMembers } from "@/lib/queries";
import type { Role } from "@/lib/types";

import { useWorkspaceLayout } from "./workspace";

const ROLES: { role: Role; help: string }[] = [
  { role: "owner", help: "Everything, including members and deleting the workspace" },
  { role: "editor", help: "Create, edit, move, delete and share pages" },
  { role: "commenter", help: "Read and comment" },
  { role: "viewer", help: "Read only" },
];

function message(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.errors?.[0]?.message ?? error.message;
  return "Something went wrong.";
}

/** Workspace members: owners add people by email, change roles and remove them. */
export function MembersPage() {
  const { workspace, setHeader } = useWorkspaceLayout();
  const me = useSession()!.user;
  const navigate = useNavigate();
  const members = useMembers(workspace.id, workspace.role !== null);
  const { add, update, remove } = useMemberMutations(workspace.id);
  const isOwner = workspace.role === "owner";
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("editor");

  useEffect(() => {
    setHeader(<span className="text-sm text-zinc-500">Members</span>);
    return () => setHeader(null);
  }, [setHeader]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    await add.mutateAsync({ email: email.trim(), role });
    setEmail("");
  };

  if (workspace.role === null) {
    return <p className="text-sm text-zinc-500">You are a guest here: you can open the pages shared with you, but not see the member list.</p>;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Members of {workspace.name}</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Members see every page in the workspace with their role. To give someone access to a single page, use Share on
          that page instead.
        </p>
      </div>

      {isOwner && (
        <form onSubmit={submit} className="flex flex-wrap gap-2 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
          <input
            type="email"
            required
            aria-label="Email"
            placeholder="Email of someone with a Ymer account"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={`${fieldClass} min-w-56 flex-1`}
          />
          <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)} className={`${fieldClass} w-32`}>
            {ROLES.map((r) => (
              <option key={r.role} value={r.role}>
                {r.role}
              </option>
            ))}
          </select>
          <button type="submit" disabled={add.isPending} className="rounded-md bg-brand-600 px-4 py-1 text-sm font-medium text-on-brand hover:bg-brand-700 disabled:opacity-60">
            Add member
          </button>
          {add.error && <p className="w-full text-xs text-rose-600">{message(add.error)}</p>}
        </form>
      )}

      <ul aria-label="Members" className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
        {members.data?.map((m) => {
          const self = m.user_id === me.id;
          return (
            <li key={m.user_id} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">
                  {m.display_name} {self && <span className="text-xs font-normal text-zinc-500">(you)</span>}
                </p>
                <p className="truncate text-xs text-zinc-500">{m.email}</p>
              </div>
              {isOwner ? (
                <select
                  aria-label={`Role of ${m.display_name}`}
                  value={m.role}
                  onChange={(e) => update.mutate({ userId: m.user_id, role: e.target.value as Role })}
                  className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
                >
                  {ROLES.map((r) => (
                    <option key={r.role} value={r.role} title={r.help}>
                      {r.role}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="text-sm text-zinc-500">{m.role}</span>
              )}
              {(isOwner || self) && (
                <button
                  type="button"
                  onClick={async () => {
                    const what = self ? "Leave this workspace?" : `Remove ${m.display_name} from the workspace? Their page shares here go too.`;
                    if (!window.confirm(what)) return;
                    await remove.mutateAsync(m.user_id);
                    if (self) navigate("/");
                  }}
                  className="rounded-md px-2 py-1 text-sm text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950"
                >
                  {self ? "Leave" : "Remove"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {(update.error || remove.error) && <p className="text-sm text-rose-600">{message(update.error ?? remove.error)}</p>}

      <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 text-xs text-zinc-500">
        {ROLES.map((r) => (
          <div key={r.role} className="contents">
            <dt className="font-medium">{r.role}</dt>
            <dd>{r.help}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
