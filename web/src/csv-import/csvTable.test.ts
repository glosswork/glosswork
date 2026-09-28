import { describe, expect, it } from "vitest";
import { parseCsv, serializeCsv } from "./csvTable";

describe("parseCsv", () => {
  it("parses plain unquoted rows", () => {
    expect(parseCsv("a,b,c\n1,2,3")).toEqual([
      ["a", "b", "c"],
      ["1", "2", "3"],
    ]);
  });

  it("parses a quoted field containing a comma", () => {
    expect(parseCsv('name,note\nAda,"Loves, cake"')).toEqual([
      ["name", "note"],
      ["Ada", "Loves, cake"],
    ]);
  });

  it("parses a quoted field containing an embedded double-quote", () => {
    expect(parseCsv('name,quip\nAda,"She said ""hi"""')).toEqual([
      ["name", "quip"],
      ["Ada", 'She said "hi"'],
    ]);
  });

  it("parses a quoted field containing an embedded newline", () => {
    expect(parseCsv('name,bio\nAda,"Line one\nLine two"')).toEqual([
      ["name", "bio"],
      ["Ada", "Line one\nLine two"],
    ]);
  });

  it("returns an empty array for empty input", () => {
    expect(parseCsv("")).toEqual([]);
  });

  it("does not produce a phantom trailing row from a final newline", () => {
    expect(parseCsv("a,b\n1,2\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("handles CRLF line endings", () => {
    expect(parseCsv("a,b\r\n1,2\r\n3,4")).toEqual([
      ["a", "b"],
      ["1", "2"],
      ["3", "4"],
    ]);
  });
});

describe("serializeCsv", () => {
  it("quotes a field containing a comma", () => {
    expect(serializeCsv([["Loves, cake"]])).toBe('"Loves, cake"');
  });

  it("doubles an embedded quote and wraps the field", () => {
    expect(serializeCsv([['She said "hi"']])).toBe('"She said ""hi"""');
  });

  it("quotes a field containing a newline", () => {
    expect(serializeCsv([["Line one\nLine two"]])).toBe('"Line one\nLine two"');
  });

  it("leaves plain fields unquoted", () => {
    expect(serializeCsv([["a", "b", "c"]])).toBe("a,b,c");
  });
});

describe("parseCsv(serializeCsv(rows)) round-trip", () => {
  it.each([
    [[["a", "b", "c"], ["1", "2", "3"]]],
    [[["Loves, cake", "plain"]]],
    [[['She said "hi"', "other"]]],
    [[["Line one\nLine two", "plain"]]],
    [[["target_date", "key"], ["2026-01-01", "INIT-1"]]],
  ])("round-trips %j", (rows) => {
    expect(parseCsv(serializeCsv(rows))).toEqual(rows);
  });
});
