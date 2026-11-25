import { describe, expect, it } from "vitest";

import type { Page } from "@/lib/types";

import {
  briefApproved,
  canBuild,
  candidateSources,
  mergeEvents,
  newAnswers,
  runActive,
  stepStates,
  type Factory,
  type RunEvent,
} from "./api";

const page = (id: string, title: string, extra: Partial<Page> = {}): Page => ({
  id,
  workspace_id: "w",
  parent_id: null,
  type: "document",
  title,
  position: 0,
  my_role: "editor",
  created_at: "2026-11-25T10:00:00Z",
  updated_at: "2026-11-25T10:00:00Z",
  ...extra,
});

const factory = (extra: Partial<Factory> = {}): Factory => ({
  page_id: "f",
  stage: "brief",
  can_edit: true,
  sources: [],
  questions: [],
  brief: null,
  brief_generated_at: null,
  approved_at: null,
  approved_by: null,
  stack: null,
  stacks: [],
  architecture: null,
  system_map: null,
  tasks: null,
  architecture_generated_at: null,
  plan_approved_at: null,
  plan_approved_by: null,
  build: { latest_run: null, file_count: 0, sandbox: "fake" },
  ...extra,
});

describe("candidateSources", () => {
  it("leaves out folders, factories, chosen pages and the factory's own pages", () => {
    const pages = [
      page("a", "Spec"),
      page("b", "Folder", { type: "folder" }),
      page("f", "The factory", { type: "factory" }),
      page("brief", "Brief", { parent_id: "f" }),
      page("c", "Chosen"),
      page("d", "Sketch", { type: "drawing" }),
    ];
    const f = factory({ sources: [{ page_id: "c", title: "Chosen", type: "document", visible: true }] });
    expect(candidateSources(pages, f, "").map((p) => p.id)).toEqual(["a", "d"]);
    expect(candidateSources(pages, f, "ske").map((p) => p.id)).toEqual(["d"]);
  });
});

describe("newAnswers", () => {
  it("counts answers given after the brief was written", () => {
    const q = (answered_at: string | null, status: "open" | "answered" = "answered") => ({
      id: answered_at ?? "x",
      question: "?",
      reason: "",
      status,
      answer: status === "answered" ? "yes" : null,
      answered_by: null,
      answered_at,
    });
    const f = factory({
      brief_generated_at: "2026-11-25T10:00:00Z",
      questions: [q("2026-11-25T09:00:00Z"), q("2026-11-25T11:00:00Z"), q(null, "open")],
    });
    expect(newAnswers(f)).toBe(1);
    expect(newAnswers(factory())).toBe(0);
  });
});

describe("stepStates", () => {
  it("marks finished, current and next stages", () => {
    expect(stepStates(factory({ stage: "brief" })).slice(0, 2)).toEqual(["current", "later"]);
    expect(stepStates(factory({ stage: "architecture" })).slice(0, 3)).toEqual(["done", "current", "later"]);
    expect(stepStates(factory({ stage: "planned" })).slice(0, 3)).toEqual(["done", "done", "current"]);
    expect(stepStates(factory({ stage: "built" })).slice(0, 4)).toEqual(["done", "done", "done", "next"]);
    expect(canBuild(factory({ stage: "architecture" }))).toBe(false);
    expect(canBuild(factory({ stage: "built" }))).toBe(true);
    expect(briefApproved(factory({ stage: "brief" }))).toBe(false);
    expect(briefApproved(factory({ stage: "planned" }))).toBe(true);
  });
});

describe("build log", () => {
  const ev = (seq: number): RunEvent => ({ seq, kind: "note", title: `line ${seq}`, detail: null, at: "" });

  it("appends only lines it has not shown", () => {
    const shown = [ev(1), ev(2)];
    expect(mergeEvents(shown, [ev(2), ev(3)]).map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(mergeEvents(shown, [])).toBe(shown);
    expect(mergeEvents([], [ev(1)]).map((e) => e.seq)).toEqual([1]);
  });

  it("knows which builds are still going", () => {
    const run = (status: string) => ({ status }) as Parameters<typeof runActive>[0];
    expect(["queued", "running", "stopping"].every((s) => runActive(run(s)))).toBe(true);
    expect(["passed", "failed", "stopped"].some((s) => runActive(run(s)))).toBe(false);
    expect(runActive(null)).toBe(false);
  });
});
