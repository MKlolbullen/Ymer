/**
 * Fetch wrapper for the Flask API (proxied at /api in development).
 *
 * Signing in sets httpOnly cookies, so every request sends `credentials: "include"` and,
 * when it changes something, the CSRF token in `X-CSRF-Token`. A 401 triggers one
 * refresh (the refresh cookie is only sent to /api/auth) and one retry.
 */
import { getCsrfToken, getSession, getStatus, rememberedUser, setCsrfToken, setSession, type Session } from "./session";
import type { User } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly errors?: { field: string; message: string }[],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function toError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    return new ApiError(response.status, body.message ?? response.statusText, body.errors);
  } catch {
    return new ApiError(response.status, `${response.status} ${response.statusText}`);
  }
}

function send(path: string, init: RequestInit & { method: string }): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const csrf = getCsrfToken();
  if (csrf && !SAFE.has(init.method)) headers.set("X-CSRF-Token", csrf);
  return fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
}

let refreshing: Promise<boolean> | null = null;

/** Swap the refresh cookie for a new access cookie. Concurrent callers share one request. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    const response = await send("/auth/refresh", { method: "POST" });
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) setSession(null);
      return false;
    }
    const body = (await response.json()) as { csrf_token?: string };
    if (body.csrf_token) setCsrfToken(body.csrf_token);
    return true;
  })()
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

const NO_RETRY = new Set(["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"]);

/** Send a request; on 401 refresh once and retry, on a CSRF 403 re-read the token and retry. */
async function request(path: string, init: RequestInit & { method: string }): Promise<Response> {
  let response = await send(path, init);
  if (NO_RETRY.has(path)) return response;
  if (response.status === 401 && getSession() && (await refreshSession())) {
    response = await send(path, init);
  } else if (response.status === 403 && !SAFE.has(init.method)) {
    const error = await response.clone().json().catch(() => null);
    if (String(error?.message ?? "").includes("CSRF") && (await reloadCsrfToken())) {
      response = await send(path, init);
    }
  }
  return response;
}

type Options = { method?: string; json?: unknown; form?: FormData; signal?: AbortSignal };

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  const response = await request(path, {
    method: options.method ?? "GET",
    headers,
    body: options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined),
    signal: options.signal,
  });
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

type MeResponse = { user: User; csrf_token?: string };

/** Ask the API who is signed in. Used on load, after another tab signs in or out, and
 * to recover the CSRF token if storage was cleared. */
export async function loadSession(): Promise<Session | null> {
  let response = await send("/auth/me", { method: "GET" }).catch(() => null);
  if (response?.status === 401 && (await refreshSession())) {
    response = await send("/auth/me", { method: "GET" }).catch(() => null);
  }
  if (!response) {
    // No network (or no API): an outage is not a sign-out. Carry on as the user last
    // signed in here, working from offline copies, and check again once back online.
    const user = rememberedUser();
    setSession(user ? { user } : null, { broadcast: false });
    if (user) window.addEventListener("online", () => void loadSession(), { once: true });
    return user ? { user } : null;
  }
  if (!response.ok) {
    if (response.status === 401) setSession(null, { broadcast: false });
    else if (getStatus() === "loading") {
      // The API answered with an error (5xx): same as no network.
      const user = rememberedUser();
      setSession(user ? { user } : null, { broadcast: false });
    }
    return null;
  }
  const body = (await response.json().catch(() => ({}))) as Partial<MeResponse>;
  if (!body.user) {
    setSession(null, { broadcast: false });
    return null;
  }
  if (body.csrf_token) setCsrfToken(body.csrf_token);
  const session = { user: body.user };
  setSession(session, { broadcast: false });
  return session;
}

async function reloadCsrfToken(): Promise<boolean> {
  const before = getCsrfToken();
  const session = await loadSession();
  return session !== null && getCsrfToken() !== before;
}

/** A two-minute token for the collab service, fetched on every (re)connect. */
export async function collabToken(): Promise<string> {
  try {
    return (await api<{ token: string }>("/auth/collab-token", { method: "POST" })).token;
  } catch {
    return "";
  }
}

/** Unauthenticated GET, kept for the health indicator. */
export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
    ...init,
  });
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

export type HealthResponse = { status: string; service: string; version: string };

/** Download a file from the API (with the login cookie) and save it under `filename`. */
export async function downloadFromApi(path: string, filename: string): Promise<void> {
  const response = await request(path, { method: "GET" });
  if (!response.ok) throw await toError(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** A server-relative URL the API returned (like /api/...), made absolute when the API is on another host. */
export function apiOrigin(path: string): string {
  return /^https?:/.test(API_BASE) ? new URL(API_BASE).origin + path : path;
}
