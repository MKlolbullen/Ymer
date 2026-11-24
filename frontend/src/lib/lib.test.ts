import { expect, test } from "vitest";

import { buildTree } from "@/components/PageTree";

import { base64ToBytes, bytesToBase64 } from "./base64";
import { secondsLeft } from "./session";
import { atLeast, type Page } from "./types";

const page = (id: string, parent: string | null, position = 0): Page => ({
  id,
  parent_id: parent,
  position,
  workspace_id: "w",
  type: "document",
  title: id,
  my_role: "editor",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

test("buildTree nests pages, sorts by position, and lifts orphans to the top", () => {
  const tree = buildTree([page("b", null, 2), page("a", null, 1), page("a1", "a"), page("x", "hidden")]);
  expect(tree.map((n) => n.page.id)).toEqual(["x", "a", "b"]);
  expect(tree[1]!.children.map((n) => n.page.id)).toEqual(["a1"]);
});

test("base64 round-trips large binary data", () => {
  const bytes = new Uint8Array(200_000).map((_, i) => i % 256);
  expect(base64ToBytes(bytesToBase64(bytes))).toEqual(bytes);
});

test("secondsLeft reads the JWT expiry and treats garbage as expired", () => {
  const exp = Math.floor(Date.now() / 1000) + 600;
  const token = `x.${btoa(JSON.stringify({ exp }))}.y`;
  expect(secondsLeft(token)).toBeGreaterThan(590);
  expect(secondsLeft("nope")).toBeLessThan(0);
});

test("role ranking", () => {
  expect(atLeast("owner", "editor")).toBe(true);
  expect(atLeast("commenter", "editor")).toBe(false);
  expect(atLeast(null, "viewer")).toBe(false);
});
