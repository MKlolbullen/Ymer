import { render } from "@testing-library/react";
import { expect, test } from "vitest";

import { ancestorsOf, dropTarget } from "@/components/PageTree";
import { highlight } from "@/components/SearchDialog";
import { matchPages } from "@/editor/mentions";
import type { Page, Property, Row } from "@/lib/types";

import { displayText, groupRows, normalizeConfig, opsFor, positionBetween, propRef, rollupFunctionsFor, rollupOpsFor } from "./logic";

const page = (id: string, parent: string | null, position = 0, title = id): Page => ({
  id,
  parent_id: parent,
  position,
  workspace_id: "w",
  type: "document",
  title,
  my_role: "editor",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

const status: Property = {
  id: "s",
  name: "Status",
  type: "select",
  position: 1,
  config: {
    options: [
      { id: "todo", name: "Todo", color: "gray" },
      { id: "done", name: "Done", color: "green" },
    ],
  },
};

const row = (id: string, values: Record<string, unknown>, position = 0): Row => ({
  id,
  position,
  values,
  created_at: "",
  updated_at: "",
});

test("groupRows makes a column per option and a leading 'no value' column only when needed", () => {
  const columns = groupRows([row("a", { s: "done" }), row("b", { s: "todo" }), row("c", {})], status);
  expect(columns.map((c) => c.option?.id ?? null)).toEqual([null, "todo", "done"]);
  expect(columns.map((c) => c.rows.map((r) => r.id))).toEqual([["c"], ["b"], ["a"]]);
  expect(groupRows([row("a", { s: "done" })], status).map((c) => c.option?.id)).toEqual(["todo", "done"]);
});

test("positionBetween finds room around and between neighbours", () => {
  expect(positionBetween(1, 2)).toBe(1.5);
  expect(positionBetween(undefined, 1)).toBe(0);
  expect(positionBetween(3, undefined)).toBe(4);
  expect(positionBetween(undefined, undefined)).toBe(1);
});

test("filter operators depend on the property type", () => {
  expect(opsFor("checkbox").map((o) => o.op)).toEqual(["equals"]);
  expect(opsFor("date").find((o) => o.op === "gt")?.label).toBe("is after");
  expect(opsFor("relation").map((o) => o.op)).toContain("contains");
});

test("displayText renders every kind of value as plain text", () => {
  expect(displayText(status, "done")).toBe("Done");
  expect(displayText({ ...status, type: "multi_select" }, ["todo", "done", "gone"])).toBe("Todo, Done");
  expect(displayText({ ...status, type: "relation", config: {} }, [{ id: "1", title: null }])).toBe("Restricted");
  expect(displayText({ ...status, type: "rollup", config: { function: "percent_checked" } }, 50)).toBe("50%");
  expect(displayText({ ...status, type: "checkbox", config: {} }, true)).toBe("✓");
  expect(displayText(status, null)).toBe("");
});

test("normalizeConfig fills in missing keys", () => {
  expect(normalizeConfig({ sorts: [{ property_id: "x", direction: "asc" }] })).toEqual({
    sorts: [{ property_id: "x", direction: "asc" }],
    filters: [],
    group_by: null,
    hidden: [],
    widths: {},
    chart: null,
  });
});

test("dropTarget places before, after and inside, and refuses cycles", () => {
  const pages = [page("a", null, 0), page("b", null, 1), page("c", null, 2), page("a1", "a", 0)];
  expect(dropTarget(pages, "c", "a", "before")).toEqual({ parent_id: null, before_id: "a" });
  expect(dropTarget(pages, "c", "a", "after")).toEqual({ parent_id: null, before_id: "b" });
  expect(dropTarget(pages, "a", "c", "after")).toEqual({ parent_id: null, before_id: null });
  expect(dropTarget(pages, "b", "a", "inside")).toEqual({ parent_id: "a", before_id: null });
  expect(dropTarget(pages, "a", "a1", "inside")).toBeNull();
  expect(dropTarget(pages, "a", "a", "before")).toBeNull();
});

test("ancestorsOf walks up to the first page I cannot see", () => {
  const pages = [page("a", "hidden"), page("b", "a"), page("c", "b")];
  expect(ancestorsOf(pages, "c").map((p) => p.id)).toEqual(["a", "b"]);
  expect(ancestorsOf(pages, "a")).toEqual([]);
});

test("search snippets highlight matches without using HTML", () => {
  const { container } = render(<p>{highlight("we ship the \u0002roadmap\u0003 <b>now</b>")}</p>);
  expect(container.querySelector("mark")?.textContent).toBe("roadmap");
  expect(container.querySelector("b")).toBeNull();
  expect(container.textContent).toBe("we ship the roadmap <b>now</b>");
});

test("matchPages prefers titles that start with the query and skips the current page", () => {
  const pages = [page("1", null, 0, "Project plan"), page("2", null, 0, "Plan B"), page("3", null, 0, "Planets")];
  expect(matchPages(pages, "plan", "3").map((p) => p.title)).toEqual(["Plan B", "Project plan"]);
});

test("engine v2 values display as text", () => {
  const prop = (type: Property["type"], config: Property["config"] = {}): Property => ({ id: "p", name: "P", type, config, position: 0 });
  expect(displayText(prop("date_range"), { start: "2026-11-02", end: "2026-12-04" })).toMatch(/2026.*→.*2026/);
  expect(displayText(prop("date_range"), { start: "2026-11-02", end: null })).not.toContain("→");
  expect(displayText(prop("file"), [{ name: "roe.pdf", url: "s3://b/roe.pdf" }, { url: "https://x.test/a.png", name: "" }])).toBe("roe.pdf, a.png");
  expect(displayText(prop("formula"), 73.825)).toBe("73.83");
  expect(displayText(prop("formula"), true)).toBe("✓");
  expect(displayText(prop("formula"), "A")).toBe("A");
  expect(displayText(prop("rollup", { function: "show_original" }), [100])).toBe("100");
  expect(displayText(prop("person"), [{ id: null, display_name: "M. Vance" }])).toBe("M. Vance");
});

test("rollup settings offer what fits the property", () => {
  const select: Property = { id: "s", name: "Severity", type: "select", config: { options: [] }, position: 0 };
  const number: Property = { id: "n", name: "CVSS", type: "number", config: {}, position: 0 };
  expect(rollupFunctionsFor(select).map((f) => f.fn)).toContain("top_option");
  expect(rollupFunctionsFor(number).map((f) => f.fn)).not.toContain("top_option");
  expect(rollupOpsFor("select").map((o) => o.op)).toContain("in");
  expect(opsFor("select").map((o) => o.op)).not.toContain("in");
  expect(opsFor("date_range")[0]?.label).toBe("includes");
  expect(propRef('Say "hi"')).toBe('prop("Say \\"hi\\"")');
});
