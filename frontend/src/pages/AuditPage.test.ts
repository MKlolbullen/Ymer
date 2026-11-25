import { expect, test } from "vitest";

import { describe as describeEvent, type AuditEvent } from "./AuditPage";

const event = (action: string, details: Record<string, unknown>): AuditEvent => ({
  id: "1",
  created_at: "2026-11-25T10:00:00Z",
  action,
  actor: null,
  target_type: null,
  target_id: null,
  ip: null,
  details,
});

test("audit events read as plain sentences", () => {
  expect(describeEvent(event("member.role", { from: "viewer", to: "editor" }))).toBe(
    "changed a member's role from viewer to editor",
  );
  expect(describeEvent(event("page.delete", { title: "Plan", pages: 3 }))).toBe(
    "deleted the page “Plan” and 2 page(s) under it",
  );
  expect(describeEvent(event("factory.push", { release: 2, remote: "https://x/y.git", branch: "main", status: "pushed" }))).toBe(
    "pushed release 2 to https://x/y.git (main): pushed",
  );
  expect(describeEvent(event("something.new", {}))).toBe("something.new");
});
