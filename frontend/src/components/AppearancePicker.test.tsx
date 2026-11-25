import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { initializeAppearance, useColorMode } from "@/lib/appearance";
import { AppearancePicker } from "./AppearancePicker";

let media: EventTarget & { matches: boolean };
let stop: () => void;

beforeEach(() => {
  media = Object.assign(new EventTarget(), { matches: false });
  vi.stubGlobal("matchMedia", () => media);
  localStorage.clear();
  stop = initializeAppearance();
});

afterEach(() => {
  cleanup();
  stop();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
});

function CanvasMode() {
  return <output>{useColorMode()}</output>;
}

test("the picker, document and native canvas mode agree for every choice", () => {
  render(<><AppearancePicker /><CanvasMode /></>);
  const picker = screen.getByRole("combobox", { name: "Theme" });
  for (const value of ["darcula", "midnight", "github", "dark", "light"]) {
    fireEvent.change(picker, { target: { value } });
    const mode = value === "light" ? "light" : "dark";
    expect(document.documentElement.dataset).toMatchObject({ palette: value, mode });
    expect(screen.getByRole("status").textContent).toBe(mode);
    expect(localStorage.getItem("ymer.appearance")).toBe(value);
  }
});

test("System follows the device while a saved palette survives a reload and OS changes", () => {
  expect(document.documentElement.dataset.palette).toBe("light");
  media.matches = true;
  media.dispatchEvent(new Event("change"));
  expect(document.documentElement.dataset.palette).toBe("dark");

  stop();
  localStorage.setItem("ymer.appearance", "midnight");
  stop = initializeAppearance();
  media.matches = false;
  media.dispatchEvent(new Event("change"));
  expect(document.documentElement.dataset.palette).toBe("midnight");
});

test("a change in another tab updates the picker and clearing storage restores System", () => {
  render(<AppearancePicker />);
  const picker = screen.getByRole<HTMLSelectElement>("combobox", { name: "Theme" });
  localStorage.setItem("ymer.appearance", "github");
  act(() => window.dispatchEvent(new StorageEvent("storage", { key: "ymer.appearance" })));
  expect(picker.value).toBe("github");
  localStorage.clear();
  act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));
  expect(picker.value).toBe("system");
  expect(document.documentElement.dataset.palette).toBe("light");
});

test("invalid saved values fall back to System and blocked storage allows an in-tab choice", () => {
  stop();
  localStorage.setItem("ymer.appearance", "toString");
  stop = initializeAppearance();
  expect(document.documentElement.dataset.palette).toBe("light");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage blocked"); });
  render(<AppearancePicker />);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "darcula" } });
  expect(document.documentElement.dataset.palette).toBe("darcula");
});
