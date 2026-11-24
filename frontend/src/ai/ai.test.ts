import { describe, expect, it } from "vitest";

import { splitCitations, textToContent } from "./api";

describe("textToContent", () => {
  it("turns paragraphs and bullet lines into TipTap nodes", () => {
    expect(textToContent("First line\nwraps here.\n\n- one\n- two\nAfter")).toEqual([
      { type: "paragraph", content: [{ type: "text", text: "First line wraps here." }] },
      {
        type: "bulletList",
        content: [
          { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "one" }] }] },
          { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "two" }] }] },
        ],
      },
      { type: "paragraph", content: [{ type: "text", text: "After" }] },
    ]);
  });

  it("ignores empty input", () => {
    expect(textToContent("  \n\n ")).toEqual([]);
  });
});

describe("splitCitations", () => {
  it("finds [n] markers", () => {
    expect(splitCitations("Book via the desk [1]. Economy only [2][3].")).toEqual([
      "Book via the desk ",
      1,
      ". Economy only ",
      2,
      3,
      ".",
    ]);
  });
});
