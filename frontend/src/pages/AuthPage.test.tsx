import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";

import { apiGet } from "@/lib/api";
import { AuthPage } from "./AuthPage";

vi.mock("@/lib/auth", () => ({ useSession: () => null }));
vi.mock("@/lib/api", () => ({ apiGet: vi.fn() }));
afterEach(cleanup);

function show(mode: "login" | "register", enabled: boolean) {
  vi.mocked(apiGet).mockResolvedValue({ enabled });
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}>
    <AuthPage mode={mode} />
  </QueryClientProvider></MemoryRouter>);
}

it("keeps a direct visit to registration closed", async () => {
  show("register", false);
  await screen.findByText("Account registration is closed on this server.");
  expect(screen.queryByLabelText("Password")).toBeNull();
  expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
});

it("offers registration from the login page on an open installation", async () => {
  show("login", true);
  expect((await screen.findByRole("link", { name: "Create an account" })).getAttribute("href")).toBe("/register");
});
