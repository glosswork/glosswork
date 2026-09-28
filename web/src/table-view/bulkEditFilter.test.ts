import { describe, expect, it } from "vitest";
import { selectedKeysFilter } from "./bulkEditFilter";

describe("selectedKeysFilter", () => {
  it("builds the key-in-[...] filter the bulk-update/bulk-delete scope trick relies on", () => {
    expect(selectedKeysFilter(["INIT-1", "INIT-2"])).toEqual({
      field: "key",
      op: "in",
      value: ["INIT-1", "INIT-2"],
    });
  });
});
