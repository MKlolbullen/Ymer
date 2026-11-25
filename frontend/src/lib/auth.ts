import { useSyncExternalStore } from "react";

import { wipeOfflineData } from "@/offline/store";

import { api, loadSession } from "./api";
import {
  getSession,
  getStatus,
  onOtherTabChange,
  setCsrfToken,
  setSession,
  subscribe,
  type Session,
  type SessionStatus,
} from "./session";
import type { User } from "./types";

type AuthResponse = { user: User; csrf_token: string };

function store(response: AuthResponse): Session {
  setCsrfToken(response.csrf_token);
  void wipeOfflineData({ keepUser: response.user.id });
  const session = { user: response.user };
  setSession(session);
  return session;
}

export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, getSession);
}

export function useSessionStatus(): SessionStatus {
  return useSyncExternalStore(subscribe, getStatus);
}

let started = false;

/** Find out who is signed in (once per page load), and follow other tabs from then on. */
export function startSession(): void {
  if (started) return;
  started = true;
  void loadSession();
  onOtherTabChange(() => void loadSession());
}

export async function login(email: string, password: string): Promise<Session> {
  return store(await api<AuthResponse>("/auth/login", { method: "POST", json: { email, password } }));
}

export async function register(
  email: string,
  password: string,
  displayName: string,
): Promise<Session> {
  return store(
    await api<AuthResponse>("/auth/register", {
      method: "POST",
      json: { email, password, display_name: displayName },
    }),
  );
}

/** Sign out and delete this device's offline copies (edits not yet synced are lost). */
export async function logout(): Promise<void> {
  try {
    await api("/auth/logout", { method: "POST" });
  } finally {
    setSession(null);
    await wipeOfflineData();
  }
}
