import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { AiSettingsPage } from "./AiSettingsPage";

vi.mock("@/lib/auth", () => ({ useSession: () => ({ user: { id: "account-id" } }) }));
vi.mock("@/lib/api", () => ({ api: vi.fn() }));

it("clears an unsaved key when switching providers and reuses the selected provider's saved key", async () => {
  const profiles = { gemini: "saved-gemini" };
  vi.mocked(api).mockResolvedValueOnce({ active: null, profiles, server_model: "demo" })
    .mockResolvedValueOnce({ active: "gemini", profiles, server_model: "demo" });
  render(<QueryClientProvider client={new QueryClient()}><AiSettingsPage /></QueryClientProvider>);
  const provider = await screen.findByLabelText("Provider");
  expect(Array.from((provider as HTMLSelectElement).options, (o) => o.text)).toEqual([
    "Server default", "Anthropic", "OpenAI", "xAI", "Gemini", "Venice.ai",
  ]);
  fireEvent.change(provider, { target: { value: "openai" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "unsaved-test-value" } });
  fireEvent.change(provider, { target: { value: "gemini" } });
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("Model ID") as HTMLInputElement).value).toBe("saved-gemini");
  fireEvent.click(screen.getByText("Save settings"));
  await screen.findByText("Settings saved.");
  expect(api).toHaveBeenLastCalledWith("/settings/ai", { method: "PUT", json: { provider: "gemini", model: "saved-gemini" } });
});
