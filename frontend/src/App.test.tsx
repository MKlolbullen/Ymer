import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";

afterEach(() => vi.unstubAllGlobals());

test("signed-out visitors land on the sign-in page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })),
  );
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Sign in to your account" })).toBeTruthy();
});
