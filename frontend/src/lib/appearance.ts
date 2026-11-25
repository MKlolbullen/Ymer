import { useSyncExternalStore } from "react";

export const appearances = {
  system: "System",
  light: "Light",
  dark: "Dark",
  darcula: "Darcula",
  midnight: "Midnight Blue/Teal",
  github: "GitHub Dark",
} as const;

export type Appearance = keyof typeof appearances;
export type Palette = Exclude<Appearance, "system">;
const storageKey = "ymer.appearance";
const subscribers = new Set<() => void>();
let preference: Appearance = "system";
let systemDark: MediaQueryList | undefined;

function readPreference(): Appearance {
  try {
    const value = localStorage.getItem(storageKey);
    if (value && Object.hasOwn(appearances, value)) return value as Appearance;
  } catch {
    // Storage is optional. The picker still works for this tab.
  }
  return "system";
}

function resolvedPalette(): Palette {
  if (preference === "system") return systemDark?.matches ? "dark" : "light";
  return preference;
}

function apply() {
  const root = document.documentElement;
  const palette = resolvedPalette();
  root.dataset.palette = palette;
  root.dataset.mode = palette === "light" ? "light" : "dark";
  subscribers.forEach((notify) => notify());
}

/** Apply the saved preference before mounting React, including on signed-out routes. */
export function initializeAppearance() {
  preference = readPreference();
  systemDark = window.matchMedia?.("(prefers-color-scheme: dark)");
  apply();

  function syncStorage(event: StorageEvent) {
    if (event.storageArea && event.storageArea !== localStorage) return;
    if (event.key !== storageKey && event.key !== null) return;
    preference = readPreference();
    apply();
  }
  systemDark?.addEventListener("change", apply);
  window.addEventListener("storage", syncStorage);
  return () => {
    systemDark?.removeEventListener("change", apply);
    window.removeEventListener("storage", syncStorage);
  };
}

export function setAppearance(value: Appearance) {
  preference = value;
  try {
    localStorage.setItem(storageKey, value);
  } catch {
    // Keep the in-memory choice when persistence is unavailable.
  }
  apply();
}

function subscribe(notify: () => void) {
  subscribers.add(notify);
  return () => { subscribers.delete(notify); };
}

export function useAppearance() {
  return useSyncExternalStore(subscribe, () => preference);
}

export function usePalette() {
  return useSyncExternalStore(subscribe, resolvedPalette);
}

export function useColorMode() {
  return usePalette() === "light" ? "light" : "dark";
}
