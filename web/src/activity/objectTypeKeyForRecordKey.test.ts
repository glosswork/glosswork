/** Tests for `objectTypeKeyForRecordKey`. */
import { describe, expect, it } from "vitest";
import { objectTypeKeyForRecordKey } from "./objectTypeKeyForRecordKey";
import type { ObjectTypeSummary } from "../api/objectTypes";

const objectTypes = [
  { key: "prospect", key_prefix: "PROS" },
  { key: "initiative", key_prefix: "INIT" },
] as ObjectTypeSummary[];

describe("objectTypeKeyForRecordKey", () => {
  it("resolves a record key to its object type through the unique key prefix", () => {
    expect(objectTypeKeyForRecordKey("PROS-005", objectTypes)).toBe("prospect");
    expect(objectTypeKeyForRecordKey("INIT-001", objectTypes)).toBe("initiative");
  });

  it("returns null rather than a link that would 404", () => {
    expect(objectTypeKeyForRecordKey("ZZZ-001", objectTypes)).toBeNull();
    expect(objectTypeKeyForRecordKey("nohyphen", objectTypes)).toBeNull();
  });

  it("returns null for an event about no record, which a cross-record feed meets routinely", () => {
    // Every schema-level audit event (`entity_type` of `object_type` or `field`) carries a
    // null `record_key`. The record page's history never did, which is why the old signature
    // took a bare string.
    expect(objectTypeKeyForRecordKey(null, objectTypes)).toBeNull();
  });
});
