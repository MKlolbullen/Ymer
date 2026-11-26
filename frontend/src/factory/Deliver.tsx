/**
 * Stage F4: review a passed build and approve it as a release, then deliver releases:
 * download a zip, preview a web app in an isolated frame, or push to a git repository.
 */
import { CircleCheck } from "lucide-react";
import { useState } from "react";

import { Icon } from "@/components/icons";
import { apiOrigin, downloadFromApi } from "@/lib/api";
import { formatDateTime, timeAgo } from "@/lib/format";
import type { Page } from "@/lib/types";

import {
  canBuild,
  previewLink,
  readyForReview,
  releaseDownloadPath,
  runActive,
  useFactoryMutations,
  useReleases,
  type Changes,
  type Factory,
  type FactoryRelease,
} from "./api";
import { errorText, primary, quiet, secondary, Section } from "./ui";

const input =
  "w-full rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900";

function slugify(text: string) {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "app"
  );
}

function ChangeList({ changes }: { changes: Changes }) {
  const rows: [string, string[], string][] = [
    ["Added", changes.added, "text-emerald-700 dark:text-emerald-400"],
    ["Changed", changes.changed, "text-amber-700 dark:text-amber-300"],
    ["Removed", changes.removed, "text-rose-700 dark:text-rose-400"],
  ];
  return (
    <ul className="max-h-48 overflow-y-auto rounded-lg border border-zinc-200 p-2 font-mono text-xs dark:border-zinc-800">
      {rows.flatMap(([label, paths, tone]) =>
        paths.map((p) => (
          <li key={`${label}-${p}`} className="flex gap-2 py-0.5">
            <span className={`w-16 shrink-0 font-sans ${tone}`}>{label}</span>
            <span className="truncate" title={p}>
              {p}
            </span>
          </li>
        )),
      )}
    </ul>
  );
}

function summary(c: Changes) {
  const parts = [
    c.added.length && `${c.added.length} added`,
    c.changed.length && `${c.changed.length} changed`,
    c.removed.length && `${c.removed.length} removed`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "no changes";
}

export function Review({ page, factory: f }: { page: Page; factory: Factory }) {
  const { release } = useFactoryMutations(page.workspace_id, page.id);
  const [notes, setNotes] = useState("");
  const open = canBuild(f);
  const latest = f.delivery.latest_release;
  const run = f.build.latest_run;
  const ready = readyForReview(f) && !runActive(run);
  const pending = f.delivery.unreleased;
  const next = (latest?.number ?? 0) + 1;
  const builtRequests = f.changes.filter((c) => c.status === "built");

  return (
    <Section
      n={6}
      title="Review"
      hint={
        open
          ? "Look over what the build changed, then approve it as a release. A release is a frozen copy: later builds do not change it."
          : "Opens once there is a build."
      }
    >
      {open &&
        (ready && pending ? (
          <div className="space-y-3">
            <p className="text-sm">
              The last build passed. Compared with {latest ? `release ${latest.number}` : "nothing released yet"}:{" "}
              <span className="font-medium">{summary(pending)}</span>.
            </p>
            <ChangeList changes={pending} />
            {builtRequests.length > 0 && (
              <p className="text-sm">
                Change requests built:{" "}
                {builtRequests.map((c, i) => (
                  <span key={c.id}>
                    {i > 0 && ", "}#{c.number} {c.title}
                  </span>
                ))}
                .
              </p>
            )}
            {f.can_edit && (
              <>
                <label className="block text-sm">
                  <span className="mb-1 block font-medium">Release notes (optional)</span>
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={2}
                    placeholder="What is new in this release"
                    className={input}
                  />
                </label>
                <button
                  onClick={() => release.mutate(notes, { onSuccess: () => setNotes("") })}
                  disabled={release.isPending}
                  className={primary}
                >
                  {release.isPending ? "Releasing…" : `Approve as release ${next}`}
                </button>
              </>
            )}
            {release.isError && (
              <p role="alert" className="text-sm text-rose-600">
                {errorText(release.error, "The release could not be made.")}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-zinc-500">
            {runActive(run)
              ? "A build is running. Review it when it has finished."
              : run?.status !== "passed"
                ? "Review opens when a build passes."
                : latest
                  ? `Release ${latest.number} matches the latest build. Build again to make changes.`
                  : "The build has not written any files yet."}
          </p>
        ))}
    </Section>
  );
}

function Preview({ page, release }: { page: Page; release: FactoryRelease }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const open = async () => {
    setLoading(true);
    setError(null);
    try {
      const link = await previewLink(page.workspace_id, page.id, release.number);
      setUrl(apiOrigin(link.url));
    } catch (e) {
      setError(errorText(e, "The preview could not be opened."));
    } finally {
      setLoading(false);
    }
  };
  if (!release.preview) return null;
  return (
    <div className="mt-4">
      {url ? (
        <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
          <div className="flex items-center justify-between gap-2 border-b border-zinc-200 bg-zinc-50 px-3 py-1.5 text-xs dark:border-zinc-800 dark:bg-zinc-900">
            <span className="text-zinc-500">
              Preview of release {release.number}. It runs isolated from Ymer, and the link works for an hour.
            </span>
            <span className="flex shrink-0 gap-1">
              <a href={url} target="_blank" rel="noreferrer noopener" className={quiet}>
                Open in a new tab
              </a>
              <button onClick={() => setUrl(null)} className={quiet}>
                Close
              </button>
            </span>
          </div>
          <iframe
            title={`Preview of release ${release.number}`}
            src={url}
            sandbox="allow-scripts allow-forms allow-modals"
            referrerPolicy="no-referrer"
            className="h-96 w-full bg-white"
          />
        </div>
      ) : (
        <button onClick={open} disabled={loading} className={secondary}>
          {loading ? "Opening…" : "Preview the app"}
        </button>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-rose-600">
          {error}
        </p>
      )}
    </div>
  );
}

function Push({ page, factory: f, release }: { page: Page; factory: Factory; release: FactoryRelease }) {
  const { push } = useFactoryMutations(page.workspace_id, page.id);
  const [remote, setRemote] = useState(f.delivery.git_remote ?? "");
  const [branch, setBranch] = useState(f.delivery.git_branch ?? "main");
  const [token, setToken] = useState("");
  const last = push.data?.push ?? null;

  return (
    <div className="mt-5">
      <h3 className="mb-1 text-sm font-medium">Push to a git repository</h3>
      <p className="mb-2 text-xs text-zinc-500">
        Ymer adds one commit with this release on top of the branch, or starts the branch. It never force-pushes. The
        token is used for this push only and is not saved. Allowed hosts: {f.delivery.git_hosts.join(", ") || "none"}.
      </p>
      {f.can_edit && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            push.mutate({ number: release.number, remote, branch, token }, { onSuccess: () => setToken("") });
          }}
          className="grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"
        >
          <label className="text-xs">
            <span className="mb-1 block text-zinc-600 dark:text-zinc-400">Repository URL</span>
            <input
              value={remote}
              onChange={(e) => setRemote(e.target.value)}
              placeholder="https://github.com/your-team/lunch-vote.git"
              required
              className={input}
            />
          </label>
          <label className="text-xs">
            <span className="mb-1 block text-zinc-600 dark:text-zinc-400">Branch</span>
            <input value={branch} onChange={(e) => setBranch(e.target.value)} required className={input} />
          </label>
          <label className="text-xs sm:col-span-2">
            <span className="mb-1 block text-zinc-600 dark:text-zinc-400">Access token with write access</span>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              placeholder="Used once, never stored"
              className={input}
            />
          </label>
          <div className="sm:col-span-2">
            <button type="submit" disabled={push.isPending || !remote.trim()} className={secondary}>
              {push.isPending ? "Pushing…" : `Push release ${release.number}`}
            </button>
          </div>
        </form>
      )}
      {push.isError && (
        <p role="alert" className="mt-2 text-sm text-rose-600">
          {errorText(push.error, "The push failed.")}
        </p>
      )}
      {last && last.status !== "failed" && (
        <p className="mt-2 text-sm text-emerald-700 dark:text-emerald-400">
          <Icon icon={CircleCheck} className="mr-1 inline-block align-[-3px]" />
          {last.status === "pushed"
            ? `Pushed to ${last.branch} as ${last.commit_sha?.slice(0, 7)}.`
            : `${last.branch} already has this release (${last.commit_sha?.slice(0, 7)}).`}
        </p>
      )}
      {release.pushes.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs" aria-label="Pushes">
          {release.pushes.map((p) => (
            <li key={p.id} className="flex flex-wrap gap-x-2 text-zinc-600 dark:text-zinc-400">
              <span className={p.status === "failed" ? "text-rose-600" : "text-emerald-700 dark:text-emerald-400"}>
                {p.status === "pushed" ? "Pushed" : p.status === "unchanged" ? "Up to date" : "Failed"}
              </span>
              <span className="font-mono">
                {p.remote.replace(/^https:\/\//, "")} · {p.branch}
                {p.commit_sha ? ` · ${p.commit_sha.slice(0, 7)}` : ""}
              </span>
              <span title={formatDateTime(p.created_at)}>
                {p.pushed_by?.display_name ?? "Someone"}, {timeAgo(p.created_at)}
              </span>
              {p.error && <span className="basis-full text-rose-600">{p.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Deliver({ page, factory: f }: { page: Page; factory: Factory }) {
  const latest = f.delivery.latest_release;
  const releases = useReleases(page.workspace_id, page.id, f.delivery.release_count > 1);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const download = (r: FactoryRelease) => {
    setDownloadError(null);
    const name = `${slugify(page.title)}-release-${r.number}.zip`;
    downloadFromApi(releaseDownloadPath(page.workspace_id, page.id, r.number), name).catch((e) =>
      setDownloadError(errorText(e, "The download failed.")),
    );
  };

  return (
    <Section
      n={7}
      title="Deliver"
      hint={
        latest
          ? "Take a release out of Ymer: download it, preview it, or push it to your repository."
          : "Opens once a build is approved as a release."
      }
    >
      {latest && (
        <>
          <div className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="rounded-full border border-emerald-500 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                Release {latest.number}
              </span>
              <span>
                {latest.file_count} files · {summary(latest.changes)}
              </span>
              <span className="text-zinc-500" title={formatDateTime(latest.created_at)}>
                Approved by {latest.approved_by?.display_name ?? "someone"}, {timeAgo(latest.created_at)}
              </span>
            </div>
            {latest.notes && (
              <p className="mt-2 whitespace-pre-wrap text-zinc-600 dark:text-zinc-300">{latest.notes}</p>
            )}
            {latest.requests.length > 0 && (
              <p className="mt-2 text-zinc-600 dark:text-zinc-300">
                Includes {latest.requests.map((r) => `#${r.number} ${r.title}`).join(", ")}.
              </p>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              <button onClick={() => download(latest)} className={primary}>
                Download .zip
              </button>
            </div>
            <p className="mt-2 text-xs text-zinc-500">
              The zip adds RELEASE.md with how to run it, a .gitignore and, for apps that run in a container, a
              Dockerfile.
            </p>
            {downloadError && (
              <p role="alert" className="mt-2 text-sm text-rose-600">
                {downloadError}
              </p>
            )}
          </div>
          <Preview key={latest.id} page={page} release={latest} />
          <Push key={`push-${latest.id}`} page={page} factory={f} release={latest} />
          {(releases.data?.length ?? 0) > 1 && (
            <div className="mt-5">
              <h3 className="mb-1 text-sm font-medium">Earlier releases</h3>
              <ul className="space-y-1 text-sm">
                {releases.data!.slice(1).map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center gap-x-3">
                    <span className="font-medium">Release {r.number}</span>
                    <span className="text-zinc-500">
                      {r.file_count} files · {timeAgo(r.created_at)}
                    </span>
                    <button onClick={() => download(r)} className={quiet}>
                      Download
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
