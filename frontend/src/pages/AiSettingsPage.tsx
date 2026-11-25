import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { aiKeys } from "@/ai/api";
import { api } from "@/lib/api";
import { useSession } from "@/lib/auth";

const providers = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  xai: "xAI",
  gemini: "Gemini",
  venice: "Venice.ai",
} as const;
type Provider = keyof typeof providers;
type Selection = Provider | "";
type Settings = {
  active: Provider | null;
  profiles: Partial<Record<Provider, string>>;
  server_model: string | null;
};

const path = "/settings/ai";
const settingsKey = (id: string) => ["ai-settings", id] as const;
const field = "mt-1.5 w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const muted = "text-sm text-zinc-600 dark:text-zinc-400";

export function AiSettingsPage() {
  const id = useSession()!.user.id;
  const query = useQuery({ queryKey: settingsKey(id), queryFn: ({ signal }) => api<Settings>(path, { signal }) });
  return (
    <section className="mx-auto max-w-xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">AI settings</h1>
        <p className={muted}>Choose the chat model you use across your workspaces.</p>
      </header>
      {query.data ? <SettingsForm key={id} id={id} settings={query.data} /> : query.isError ? (
        <p role="alert">Could not load your settings. <button className="underline" onClick={() => void query.refetch()}>Try again</button></p>
      ) : <p role="status">Loading settings…</p>}
    </section>
  );
}

function SettingsForm({ id, settings }: { id: string; settings: Settings }) {
  const qc = useQueryClient();
  const [provider, setProvider] = useState<Selection>(settings.active ?? "");
  const [model, setModel] = useState(settings.active ? settings.profiles[settings.active] ?? "" : "");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const activeModel = settings.active ? settings.profiles[settings.active] : settings.server_model;
  const saved = !!provider && !!settings.profiles[provider];

  function select(next: Selection, data = settings) {
    setProvider(next);
    setModel(next ? data.profiles[next] ?? "" : "");
    setKey("");
    setError("");
    setMessage("");
  }

  async function update(remove: boolean) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = remove
        ? await api<Settings>(`${path}/${provider}`, { method: "DELETE" })
        : await api<Settings>(path, {
          method: "PUT",
          json: provider
            ? { provider, model: model.trim(), ...(key.trim() ? { api_key: key.trim() } : {}) }
            : { provider: null },
        });
      await qc.cancelQueries({ queryKey: settingsKey(id) });
      qc.setQueryData(settingsKey(id), data);
      select(data.active ?? "", data);
      setMessage(remove ? "Saved key removed." : "Settings saved.");
      void qc.invalidateQueries({ queryKey: aiKeys.status });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update settings.");
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void update(false);
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-zinc-200 p-5 dark:border-zinc-700 sm:p-6">
      <fieldset disabled={busy} className="space-y-5">
        <legend className="mb-3 font-semibold">Chat provider</legend>
        <p className={muted}>Currently active · {settings.active ? providers[settings.active] : "Server default"}{activeModel && ` · ${activeModel}`}</p>
        {!settings.server_model && <p className={muted}>The administrator has disabled chat on this server. You can still manage your saved keys.</p>}
        <label className="block text-sm font-medium">Provider
          <select className={field} value={provider} onChange={(e) => select(e.target.value as Selection)}>
            <option value="">Server default</option>
            {Object.entries(providers).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
          </select>
        </label>
        {provider && <>
          <div>
            <label className="block text-sm font-medium">Model ID
              <input className={field} required maxLength={200} value={model} onChange={(e) => setModel(e.target.value)} spellCheck={false} autoCapitalize="none" aria-describedby="model-help" />
            </label>
            <p id="model-help" className={muted}>Enter the exact model ID available in your {providers[provider]} account.</p>
          </div>
          <div>
            <label className="block text-sm font-medium">API key
              <input className={field} type="password" autoComplete="new-password" spellCheck={false} autoCapitalize="none" maxLength={4096} required={!saved} value={key} onChange={(e) => setKey(e.target.value)} aria-describedby="key-help" />
            </label>
            <p id="key-help" className={muted}>{saved ? "A key is saved. Leave blank to keep it or enter a replacement." : "Your key is encrypted on the Ymer server."}</p>
          </div>
          <p className={muted}>AI requests send relevant workspace content to {providers[provider]} and may incur charges on your account.</p>
        </>}
        <div className="flex flex-wrap items-center gap-4">
          <button className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-on-brand hover:bg-brand-700 disabled:opacity-60" disabled={busy || (!!provider && (!model.trim() || (!saved && !key.trim())))}>{busy ? "Saving…" : "Save settings"}</button>
          {provider && saved && <button type="button" className="text-sm underline" onClick={() => {
            if (window.confirm(`Remove the saved ${providers[provider]} key and model?`)) void update(true);
          }}>Remove saved key</button>}
        </div>
      </fieldset>
      {message && <p className="mt-3 text-sm" role="status">{message}</p>}
      {error && <p className="mt-3 text-sm" role="alert">{error}</p>}
    </form>
  );
}
