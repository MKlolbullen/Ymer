import { describe, expect, it } from "vitest";

import type { Page } from "@/lib/types";

import { passRateText, stepRows } from "./Checks";
import {
  briefApproved,
  budgetShare,
  canBuild,
  candidateSources,
  mergeEvents,
  newAnswers,
  openChanges,
  plannedChanges,
  readyForReview,
  releaseBlocked,
  runActive,
  stepStates,
  tokens,
  type ChangeRequest,
  type CiRun,
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
  delivery: {
    latest_release: null,
    release_count: 0,
    unreleased: null,
    git_remote: null,
    git_branch: null,
    git_hosts: ["github.com"],
  },
  changes: [],
  spec_changes: [],
  budget: {
    limit: 20_000_000,
    used: 0,
    remaining: 20_000_000,
    period_start: "2026-11-01T00:00:00+00:00",
    custom: false,
    default_limit: 20_000_000,
    can_edit: false,
  },
  checks: {
    latest: null,
    recent: [],
    pass_rate: null,
    page_id: null,
    auto_fix: false,
    fix_streak: 0,
    max_auto_fixes: 3,
    gate: true,
    release_blocker: null,
  },
  ...extra,
});

const change = (number: number, status: ChangeRequest["status"]): ChangeRequest => ({
  id: `c${number}`,
  number,
  title: `Change ${number}`,
  detail: "",
  origin: "review",
  status,
  tasks: [],
  release: null,
  created_by: null,
  created_at: "2026-11-25T10:00:00Z",
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
    expect(stepStates(factory({ stage: "built" })).slice(0, 4)).toEqual(["done", "done", "done", "current"]);
    expect(stepStates(factory({ stage: "released" }))).toEqual(["done", "done", "done", "done", "current"]);
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

describe("review", () => {
  const passed = { status: "passed" } as Factory["build"]["latest_run"];
  const delivery = (unreleased: Factory["delivery"]["unreleased"]) => ({
    ...factory().delivery,
    unreleased,
  });

  it("is ready when a passed build changed files since the last release", () => {
    const changed = { added: ["a.py"], changed: [], removed: [] };
    const none = { added: [], changed: [], removed: [] };
    const f = (run: Factory["build"]["latest_run"], unreleased: Factory["delivery"]["unreleased"]) =>
      factory({ stage: "built", build: { latest_run: run, file_count: 1, sandbox: "fake" }, delivery: delivery(unreleased) });
    expect(readyForReview(f(passed, changed))).toBe(true);
    expect(readyForReview(f(passed, none))).toBe(false);
    expect(readyForReview(f({ status: "failed" } as Factory["build"]["latest_run"], changed))).toBe(false);
    expect(readyForReview(f(null, changed))).toBe(false);
  });
});

describe("iterating", () => {
  it("separates open from planned change requests", () => {
    const f = factory({ stage: "released", changes: [change(1, "released"), change(2, "open"), change(3, "planned")] });
    expect(openChanges(f).map((c) => c.number)).toEqual([2]);
    expect(plannedChanges(f).map((c) => c.number)).toEqual([3]);
  });

  it("shows token counts short and the budget share capped", () => {
    expect(tokens(950)).toBe("950");
    expect(tokens(12_400)).toBe("12k");
    expect(tokens(1_500_000)).toBe("1.5M");
    expect(tokens(2_000_000)).toBe("2M");
    expect(tokens(20_000_000)).toBe("20M");
    const b = factory().budget;
    expect(budgetShare({ ...b, used: 5_000_000 })).toBe(0.25);
    expect(budgetShare({ ...b, used: 50_000_000 })).toBe(1);
    expect(budgetShare({ ...b, limit: 0, used: 5 })).toBe(0);
  });
});

const ciRun = (extra: Partial<CiRun> = {}): CiRun => ({
  id: "ci1",
  number: 1,
  status: "running",
  trigger: "build",
  run_id: "r1",
  stack: "python-cli",
  sandbox: "fake",
  simulated: true,
  summary: null,
  file_count: 4,
  steps: [],
  change: null,
  fix_run_id: null,
  started_by: null,
  created_at: "2026-11-27T10:00:00Z",
  started_at: "2026-11-27T10:00:01Z",
  finished_at: null,
  ...extra,
});

describe("checks", () => {
  it("shows the steps that ran and the ones still to come", () => {
    const lint = { key: "lint", name: "Lint", command: "sh .ymer-ci/lint.sh", status: "passed" as const };
    const step = { ...lint, exit_code: 0, timed_out: false, seconds: 1.2, output: "ok" };
    const rows = stepRows(ciRun({ steps: [step] }));
    expect(rows.map((r) => [r.key, r.state])).toEqual([
      ["lint", "passed"],
      ["build", "running"],
      ["test", "waiting"],
      ["smoke", "waiting"],
    ]);
    // A finished run shows only what ran.
    expect(stepRows(ciRun({ status: "error", steps: [step] })).map((r) => r.key)).toEqual(["lint"]);
  });

  it("summarises the pass rate over finished runs", () => {
    const recent = [
      { number: 3, status: "passed" as const, created_at: "" },
      { number: 2, status: "failed" as const, created_at: "" },
      { number: 1, status: "error" as const, created_at: "" },
    ];
    const f = factory();
    expect(passRateText(f)).toBeNull();
    expect(passRateText(factory({ checks: { ...f.checks, recent, pass_rate: 0.5 } }))).toBe(
      "50% pass rate (1 of 2 recent runs)",
    );
  });

  it("blocks the release only while the gate is on", () => {
    const f = factory();
    const blocked = { ...f.checks, release_blocker: "Run the checks on this build before releasing it." };
    expect(releaseBlocked(factory({ checks: blocked }))).toBe(true);
    expect(releaseBlocked(factory({ checks: { ...blocked, gate: false } }))).toBe(false);
    expect(releaseBlocked(f)).toBe(false);
  });
});
