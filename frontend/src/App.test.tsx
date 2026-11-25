import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import { api } from "./lib/api";
import { setCsrfToken } from "./lib/session";

afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("signed-out visitors land on the sign-in page", async () => {
  const fetch = vi.fn(async (url: string) =>
    url.includes("/auth/") ? json({ message: "Sign in first." }, 401) : json({}),
  );
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Sign in to your account" })).toBeTruthy();
  // The session is read from the cookie by the server, never from browser storage.
  const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/auth/me");
  expect(init.credentials).toBe("include");
});

test("requests that change something carry the CSRF token, reads do not", async () => {
  const fetch = vi.fn(async (_url: string, _init: RequestInit) => json({ ok: true }));
  vi.stubGlobal("fetch", fetch);
  setCsrfToken("csrf-123");
  await api("/workspaces");
  await api("/workspaces", { method: "POST", json: { name: "x" } });
  const header = (i: number) => new Headers(fetch.mock.calls[i]![1].headers).get("X-CSRF-Token");
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(header(0)).toBeNull();
  expect(header(1)).toBe("csrf-123");
  expect(fetch.mock.calls.every(([, init]) => init.credentials === "include")).toBe(true);
  setCsrfToken(null);
});
