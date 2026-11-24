/**
 * The signed-in session: tokens and user, kept in localStorage so a reload stays signed in.
 *
 * Assumption: tokens live in localStorage for now. Moving the refresh token to an
 * httpOnly cookie is a hardening step for later (README Phase 5).
 */
import type { User } from "./types";

export type Session = { accessToken: string; refreshToken: string; user: User };
type Listener = (session: Session | null) => void;

const KEY = "ymer.session";
const listeners = new Set<Listener>();
let current: Session | null = read();

function read(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function getSession(): Session | null {
  return current;
}

export function setSession(session: Session | null): void {
  current = session;
  try {
    if (session) localStorage.setItem(KEY, JSON.stringify(session));
    else localStorage.removeItem(KEY);
  } catch {
    // Storage unavailable (private mode): the session lasts until the tab closes.
  }
  listeners.forEach((listener) => listener(session));
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Seconds until the JWT expires (negative when expired), read from its payload. */
export function secondsLeft(token: string): number {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]!.replace(/-/g, "+").replace(/_/g, "/")));
    return payload.exp - Date.now() / 1000;
  } catch {
    return -1;
  }
}

// Other tabs signing in or out.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== KEY) return;
    current = read();
    listeners.forEach((listener) => listener(current));
  });
}
