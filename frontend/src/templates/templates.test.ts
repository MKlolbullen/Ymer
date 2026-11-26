import { describe, expect, it } from "vitest";

import { describeSummary, importable } from "./api";

describe("import summaries", () => {
  it("says what was added in one sentence", () => {
    expect(describeSummary({ pages: 1, files: 0, warnings: [] })).toBe("Added 1 page.");
    expect(describeSummary({ pages: 5, databases: 1, rows: 4, files: 2, warnings: [] })).toBe(
      "Added 5 pages, 1 database with 4 rows and 2 files.",
    );
  });

  it("mentions the links between imported tables", () => {
    expect(describeSummary({ pages: 6, databases: 5, rows: 54, files: 0, relations: 7, links: 52, lookups: 0, warnings: [] })).toBe(
      "Added 6 pages and 5 databases with 54 rows. Linked the tables with 7 relations (52 links).",
    );
    expect(describeSummary({ pages: 7, databases: 6, rows: 86, files: 0, relations: 8, links: 1, lookups: 5, warnings: [] })).toBe(
      "Added 7 pages and 6 databases with 86 rows. Linked the tables with 8 relations (1 link) and 5 lookups.",
    );
  });

  it("accepts zips, Markdown, CSV and JSON only", () => {
    const file = (name: string) => new File(["x"], name);
    expect(importable(file("Export-1234.zip"))).toBe(true);
    expect(importable(file("Notes.MD"))).toBe(true);
    expect(importable(file("tasks.csv"))).toBe(true);
    expect(importable(file("assets.json"))).toBe(true);
    expect(importable(file("photo.png"))).toBe(false);
  });
});
