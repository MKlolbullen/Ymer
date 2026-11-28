/** Settings from environment variables (and the repo-root .env in development). */

export type Config = {
  port: number;
  /** Base URL of the Flask app, without a trailing slash. */
  backendUrl: string;
  /** Must match YMER_COLLAB_SECRET on the Flask side. */
  collabSecret: string;
  /** Milliseconds to wait after the last change before saving. */
  debounce: number;
  /** Longest a document may stay unsaved while edits keep coming. */
  maxDebounce: number;
  quiet: boolean;
  /** Reported by /metrics; the release images set YMER_VERSION. */
  version: string;
};

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const production = env.YMER_ENV === "production";
  const collabSecret = env.YMER_COLLAB_SECRET ?? "change-me-collab";
  if (production && (collabSecret === "change-me-collab" || collabSecret.length < 32)) {
    throw new Error("Set YMER_COLLAB_SECRET to a random value of at least 32 characters.");
  }
  return {
    port: Number(env.YMER_COLLAB_PORT ?? 1234),
    backendUrl: (env.YMER_COLLAB_BACKEND_URL ?? "http://localhost:5000").replace(/\/+$/, ""),
    collabSecret,
    debounce: Number(env.YMER_COLLAB_DEBOUNCE_MS ?? 2000),
    maxDebounce: Number(env.YMER_COLLAB_MAX_DEBOUNCE_MS ?? 10000),
    quiet: env.YMER_ENV === "test",
    version: env.YMER_VERSION ?? "dev",
  };
}
