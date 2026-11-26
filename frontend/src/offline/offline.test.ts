import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { collabNotice } from "@/editor/collab";
import { loadSession } from "@/lib/api";
import { getSession, getStatus, rememberedUser, setSession } from "@/lib/session";

import {
  clearPending,
  docName,
  isKnown,
  markKnown,
  markPending,
  pendingPages,
  wipeOfflineData,
} from "./store";

const user = { id: "u1", email: "a@example.com", display_name: "Ada" };

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

test("pending pages are remembered per user and cleared once synced", () => {
  markPending("u1", "p1");
  markPending("u1", "p1");
  markPending("u2", "p9");
  expect(pendingPages("u1")).toEqual(["p1"]);
  // A page with offline edits counts as known: its local copy is a real starting point.
  expect(isKnown("u1", "p1")).toBe(true);
  clearPending("u1", "p1");
  expect(pendingPages("u1")).toEqual([]);
  expect(isKnown("u1", "p1")).toBe(true);
  expect(pendingPages("u2")).toEqual(["p9"]);
  clearPending("u2", "p9");
  expect(docName("u1", "p1")).toBe("ymer-doc:u1:p1");
});

test("signing in as someone else wipes the other users' offline data, signing out wipes all", async () => {
  const deleted: string[] = [];
  vi.stubGlobal("indexedDB", {
    deleteDatabase: (name: string) => {
      deleted.push(name);
      const request = {} as { onsuccess?: () => void };
      setTimeout(() => request.onsuccess?.());
      return request;
    },
  });
  const cacheDelete = vi.fn(async () => true);
  vi.stubGlobal("caches", { delete: cacheDelete });

  markKnown("u3", "p1");
  markPending("u4", "p2");
  await wipeOfflineData({ keepUser: "u3" });
  expect(deleted).toContain("ymer-doc:u4:p2");
  expect(deleted.filter((name) => name.startsWith("ymer-doc:u3:"))).toEqual([]);
  expect(isKnown("u3", "p1")).toBe(true);
  expect(pendingPages("u4")).toEqual([]);
  expect(cacheDelete).toHaveBeenCalledWith("ymer-api");

  deleted.length = 0;
  await wipeOfflineData();
  expect(deleted).toEqual(["ymer-doc:u3:p1"]);
  expect(isKnown("u3", "p1")).toBe(false);
});

test("without a network the app starts as the user last signed in here", async () => {
  setSession({ user });
  expect(rememberedUser()).toEqual(user);
  vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
  setSession(null, { broadcast: false });
  // setSession(null) forgets the user (an explicit sign-out), so remember them again.
  localStorage.setItem("ymer.user", JSON.stringify(user));
  expect(await loadSession()).toEqual({ user });
  expect(getSession()).toEqual({ user });
  expect(getStatus()).toBe("signed-in");
});

test("a definite 401 still signs out, even with a remembered user", async () => {
  localStorage.setItem("ymer.user", JSON.stringify(user));
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
  expect(await loadSession()).toBeNull();
  expect(getStatus()).toBe("signed-out");
  expect(rememberedUser()).toBeNull();
});

test("the connection banner explains offline editing and missing copies", () => {
  expect(collabNotice({ status: "connected", localCopy: true }, true)).toBe("");
  expect(collabNotice({ status: "connecting", localCopy: null }, true)).toBe("Connecting…");
  expect(collabNotice({ status: "connecting", localCopy: true }, false)).toMatch(/saved on this device/);
  expect(collabNotice({ status: "disconnected", localCopy: false }, false)).toMatch(/hasn't been opened on this device/);
  expect(collabNotice({ status: "denied", localCopy: true }, false)).toMatch(/no longer have access/);
});
