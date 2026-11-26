import { useEffect, useState } from "react";

/**
 * Registers the service worker (production builds only) and offers a reload when a new
 * version of the app has been downloaded. The worker caches the app shell and recent
 * API reads so Ymer opens without a network; see vite.config.ts.
 */
export function UpdatePrompt() {
  const [update, setUpdate] = useState<((reload?: boolean) => Promise<void>) | null>(null);

  useEffect(() => {
    if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
    let cancelled = false;
    void import("virtual:pwa-register").then(({ registerSW }) => {
      if (cancelled) return;
      const updateSW = registerSW({
        onNeedRefresh: () => setUpdate(() => updateSW),
        // Check for a new version every hour for tabs that stay open for days.
        onRegisteredSW: (_url, registration) => {
          if (registration) setInterval(() => void registration.update(), 60 * 60 * 1000);
        },
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!update) return null;
  return (
    <div
      role="status"
      className="fixed inset-x-3 bottom-[calc(env(safe-area-inset-bottom)+0.75rem)] z-50 mx-auto flex max-w-sm items-center gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
    >
      <span className="flex-1">A new version of Ymer is ready.</span>
      <button onClick={() => setUpdate(null)} className="text-zinc-500 hover:text-zinc-900 dark:hover:text-white">
        Later
      </button>
      <button
        onClick={() => void update(true)}
        className="rounded-md bg-brand-600 px-3 py-1 font-medium text-on-brand hover:bg-brand-700"
      >
        Reload
      </button>
    </div>
  );
}
