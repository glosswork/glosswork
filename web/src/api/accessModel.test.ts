/**
 * The frontend's two deliberate second copies of a backend rule:
 * `roleScope` mirrors `services/principals.py::role_scope`, and `levelAllows` mirrors
 * `auth.py::level_allows`. Neither can be generated: `role` and `your_access` both reach the
 * SPA through routes FastAPI types as `dict[str, Any]`, which `schema.ts` therefore types as
 * `{ [key: string]: unknown }`. The mitigation is this file, which pins both mappings
 * exhaustively so a fourth role or a fifth level fails here rather than defaulting silently.
 *
 * They are tested together because they are the same idea at two grains: a browser session's
 * credential scope is `roleScope(role)`, and `your_access` is already
 * `min(credential scope, granted level)` by the time the UI sees it.
 */
import { describe, expect, it } from "vitest";
import { PRINCIPAL_ROLES, roleScope } from "./principals";
import { LEVELS, levelAllows, type Level } from "./objectTypes";

describe("roleScope", () => {
  it("maps each role exactly as services/principals.py::role_scope does", () => {
    expect(roleScope("admin")).toBe("admin");
    expect(roleScope("creator")).toBe("admin");
    expect(roleScope("member")).toBe("write");
  });

  it("pins the role vocabulary, so a fourth role fails loudly rather than defaulting", () => {
    const mapping = Object.fromEntries(PRINCIPAL_ROLES.map((role) => [role, roleScope(role)]));
    expect(mapping).toEqual({ admin: "admin", creator: "admin", member: "write" });
  });
});

describe("levelAllows", () => {
  it("pins the level vocabulary and its order", () => {
    expect(LEVELS).toEqual(["none", "read", "write", "admin"]);
  });

  /**
   * Twelve pairs, not sixteen: `required` is `Exclude<Level, "none">`, mirroring the backend's
   * `level_allows(actual: Level, required: Scope)` where `Scope` has no `none`. Asking whether
   * a level clears "none" is not a question any call site can pose.
   */
  const expected: Record<Level, Record<Exclude<Level, "none">, boolean>> = {
    none: { read: false, write: false, admin: false },
    read: { read: true, write: false, admin: false },
    write: { read: true, write: true, admin: false },
    admin: { read: true, write: true, admin: true },
  };

  for (const actual of LEVELS) {
    for (const required of ["read", "write", "admin"] as const) {
      it(`${actual} ${expected[actual][required] ? "clears" : "does not clear"} ${required}`, () => {
        expect(levelAllows(actual, required)).toBe(expected[actual][required]);
      });
    }
  }
});
