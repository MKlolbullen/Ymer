import { useSyncExternalStore } from "react";

import { api } from "./api";
import { getSession, setSession, subscribe, type Session } from "./session";
import type { User } from "./types";

type AuthResponse = { user: User; access_token: string; refresh_token: string };

function store(response: AuthResponse): Session {
  const session = {
    user: response.user,
    accessToken: response.access_token,
    refreshToken: response.refresh_token,
  };
  setSession(session);
  return session;
}

export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, getSession);
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

export async function logout(): Promise<void> {
  try {
    await api("/auth/logout", { method: "POST" });
  } finally {
    setSession(null);
  }
}
