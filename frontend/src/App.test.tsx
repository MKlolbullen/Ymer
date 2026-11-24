import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";

afterEach(() => vi.unstubAllGlobals());

test("renders the shell and reports API health", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ status: "ok", service: "ymer-api", version: "0.1.0" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);

  expect(screen.getByRole("heading", { name: "Welcome to Ymer" })).toBeTruthy();
  expect((await screen.findAllByText("API 0.1.0")).length).toBeGreaterThan(0);
});
