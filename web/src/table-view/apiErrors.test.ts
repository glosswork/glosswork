import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import {
  parseApiError,
  parseForbidden,
  parseRelationBlocked,
  parseVersionConflict,
} from "./apiErrors";

function errorBody(code: string, details: Record<string, unknown>): string {
  return JSON.stringify({ error: { code, message: "boom", details } });
}

describe("parseApiError", () => {
  it("parses the {error: {code, message, details}} envelope", () => {
    const error = new ApiError(409, errorBody("version_conflict", { current_version: 3 }));
    expect(parseApiError(error)).toEqual({
      code: "version_conflict",
      message: "boom",
      details: { current_version: 3 },
    });
  });

  it("returns null for a non-ApiError", () => {
    expect(parseApiError(new Error("network down"))).toBeNull();
  });

  it("returns null for a non-JSON body", () => {
    expect(parseApiError(new ApiError(500, "not json"))).toBeNull();
  });
});

describe("parseVersionConflict", () => {
  it("returns the typed details for a 409 version_conflict", () => {
    const details = {
      record_key: "INIT-1",
      current_version: 9,
      supplied_version: 7,
      conflicting_fields: { status: { your_value: "a", current_value: "b" } },
      changed_since_your_version: ["status"],
    };
    const error = new ApiError(409, errorBody("version_conflict", details));
    expect(parseVersionConflict(error)).toEqual(details);
  });

  it("returns null for a 409 that isn't a version conflict", () => {
    const error = new ApiError(409, errorBody("some_other_conflict", {}));
    expect(parseVersionConflict(error)).toBeNull();
  });

  it("returns null for a non-409 error", () => {
    const error = new ApiError(400, errorBody("version_conflict", {}));
    expect(parseVersionConflict(error)).toBeNull();
  });
});

describe("parseRelationBlocked", () => {
  it("returns the blocking record keys", () => {
    const error = new ApiError(
      409,
      errorBody("relation_blocked", { record_key: "PERSON-1", blocking_record_keys: ["INIT-1", "INIT-2"] }),
    );
    expect(parseRelationBlocked(error)).toEqual(["INIT-1", "INIT-2"]);
  });

  it("returns null for an unrelated error", () => {
    const error = new ApiError(409, errorBody("version_conflict", {}));
    expect(parseRelationBlocked(error)).toBeNull();
  });
});

/**
 * `forbidden` is the per-type grant refusal's code, and its message is deliberately written to
 * be actionable — it names the type, the level held, the level required, and who can fix it.
 * Without this parser it would fall through to the generic `Alert ... error={...}` path like a
 * network failure.
 */
describe("parseForbidden", () => {
  /** The exact string `ForbiddenError.__init__` builds (`src/glosswork/errors.py:255-272`). */
  const REAL_MESSAGE =
    "Your access to object type 'initiative' is 'read'; this call needs at least 'write'. " +
    "Ask an administrator of 'initiative', or a system administrator, to raise it.";

  function forbiddenBody(message: string): string {
    return JSON.stringify({
      error: {
        code: "forbidden",
        message,
        details: { object_type: "initiative", held: "read", required: "write" },
      },
    });
  }

  it("returns the backend's message verbatim, character for character", () => {
    const parsed = parseForbidden(new ApiError(403, forbiddenBody(REAL_MESSAGE)));
    expect(parsed?.message).toBe(REAL_MESSAGE);
  });

  it("carries the three details the message is built from", () => {
    const parsed = parseForbidden(new ApiError(403, forbiddenBody(REAL_MESSAGE)));
    expect(parsed).toEqual({
      message: REAL_MESSAGE,
      objectType: "initiative",
      held: "read",
      required: "write",
    });
  });

  it("returns null for a 403 carrying any other code", () => {
    // `insufficient_scope` is also a 403 and is a different situation with a different remedy
    // (present a stronger credential, not ask for a grant), which is why it has its own code
    // rather than one 403 with prose to parse.
    const error = new ApiError(403, errorBody("insufficient_scope", {}));
    expect(parseForbidden(error)).toBeNull();
  });

  it("returns null for a non-403 error and for a network failure", () => {
    expect(parseForbidden(new ApiError(409, errorBody("version_conflict", {})))).toBeNull();
    expect(parseForbidden(new Error("network down"))).toBeNull();
  });

  it("tolerates a role-axis forbidden, whose details name no object type", () => {
    // `ForbiddenError.for_role` reuses the code for the role axis and carries
    // different details. It must still be recognised, and must not invent a type name.
    const error = new ApiError(
      403,
      JSON.stringify({
        error: {
          code: "forbidden",
          message: "This route requires the 'admin' role; you hold 'member'.",
          details: { required_role: "admin", actual_role: "member" },
        },
      }),
    );
    const parsed = parseForbidden(error);
    expect(parsed?.message).toBe("This route requires the 'admin' role; you hold 'member'.");
    expect(parsed?.objectType).toBeNull();
  });
});
