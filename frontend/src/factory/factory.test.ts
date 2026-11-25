import { describe, expect, it } from "vitest";

import type { Page } from "@/lib/types";

import { candidateSources, newAnswers, type Factory } from "./api";

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
