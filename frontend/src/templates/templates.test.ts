import { describe, expect, it } from "vitest";

import { describeSummary, importable } from "./api";

describe("import summaries", () => {
  it("says what was added in one sentence", () => {
    expect(describeSummary({ pages: 1, files: 0, warnings: [] })).toBe("Added 1 page.");
    expect(describeSummary({ pages: 5, databases: 1, rows: 4, files: 2, warnings: [] })).toBe(
      "Added 5 pages, 1 database with 4 rows and 2 files.",
    );
  });

  it("accepts zips, Markdown and CSV only", () => {
    const file = (name: string) => new File(["x"], name);
    expect(importable(file("Export-1234.zip"))).toBe(true);
    expect(importable(file("Notes.MD"))).toBe(true);
    expect(importable(file("tasks.csv"))).toBe(true);
    expect(importable(file("photo.png"))).toBe(false);
  });
});
