/** Fetch wrapper for the Flask API (proxied at /api in development). */
import { getSession, secondsLeft, setSession } from "./session";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

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

let refreshing: Promise<string | null> | null = null;

/** Swap the refresh token for new tokens. Concurrent callers share one request. */
function refreshTokens(): Promise<string | null> {
  refreshing ??= (async () => {
    const session = getSession();
    if (!session) return null;
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: session.refreshToken }),
    });
    if (!response.ok) {
      if (response.status === 401) setSession(null);
      return null;
    }
    const tokens = await response.json();
    setSession({ ...session, accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
    return tokens.access_token as string;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

/** A valid access token, refreshed first when it is about to expire. */
export async function accessToken(): Promise<string | null> {
  const session = getSession();
  if (!session) return null;
  if (secondsLeft(session.accessToken) > 30) return session.accessToken;
  return refreshTokens();
}

type Options = { method?: string; json?: unknown; form?: FormData; signal?: AbortSignal };

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const send = (token: string | null) => {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.json !== undefined) headers["Content-Type"] = "application/json";
    return fetch(`${API_BASE}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined),
      signal: options.signal,
    });
  };
  let response = await send(await accessToken());
  if (response.status === 401 && getSession()) {
    const token = await refreshTokens();
    if (token) response = await send(token);
  }
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
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
