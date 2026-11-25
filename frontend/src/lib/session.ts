/**
 * The signed-in session, as far as the app can see it.
 *
 * The login tokens themselves live in httpOnly cookies that JavaScript cannot read, so
 * a script injected into the page cannot steal them. What the app keeps is the user it
 * is signed in as and the CSRF token, which it echoes in `X-CSRF-Token` on requests that
 * change something. The CSRF token is not a credential on its own (it is useless
 * without the cookies), so keeping it in localStorage across reloads is fine.
 *
 * On load the session is unknown ("loading") until `GET /api/auth/me` answers.
 */
import type { User } from "./types";

export type Session = { user: User };
export type SessionStatus = "loading" | "signed-in" | "signed-out";
type Listener = () => void;

const CSRF_KEY = "ymer.csrf";
/** Written on sign-in and sign-out so other tabs notice (holds no secret). */
const SIGNAL_KEY = "ymer.auth";
const listeners = new Set<Listener>();
let current: Session | null = null;
let status: SessionStatus = "loading";

function notify(): void {
  listeners.forEach((listener) => listener());
}

export function getSession(): Session | null {
  return current;
}

export function getStatus(): SessionStatus {
  return status;
}

export function setSession(session: Session | null, { broadcast = true } = {}): void {
  const changedUser = current?.user.id !== session?.user.id;
  current = session;
  status = session ? "signed-in" : "signed-out";
  if (!session) setCsrfToken(null);
  if (broadcast && changedUser) {
    try {
      localStorage.setItem(SIGNAL_KEY, `${session?.user.id ?? ""}:${Date.now()}`);
    } catch {
      // Storage unavailable: other tabs find out on their next request.
    }
  }
  notify();
}

export function getCsrfToken(): string | null {
  try {
    return localStorage.getItem(CSRF_KEY);
  } catch {
    return memoryCsrf;
  }
}

let memoryCsrf: string | null = null;

export function setCsrfToken(token: string | null): void {
  memoryCsrf = token;
  try {
    if (token) localStorage.setItem(CSRF_KEY, token);
    else localStorage.removeItem(CSRF_KEY);
  } catch {
    // private mode: kept in memory for this tab
  }
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const crossTab = new Set<() => void>();

/** Run `callback` when another tab signs in or out. */
export function onOtherTabChange(callback: () => void): () => void {
  crossTab.add(callback);
  return () => crossTab.delete(callback);
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === SIGNAL_KEY) crossTab.forEach((callback) => callback());
  });
}
