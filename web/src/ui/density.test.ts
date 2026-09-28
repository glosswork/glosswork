/**
 * docs/DESIGN.md 2.4's two answers to one question, and the guard every storage read needs.
 *
 * The *geometry* these choices produce is not testable here: `getBoundingClientRect` returns
 * zeroes in jsdom, so the 38/32px row heights are a Playwright assertion (`e2e/density.spec.ts`)
 * or they are not proven.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DENSITY_STORAGE_KEY,
  isDensity,
  readDensity,
  resolveDensity,
  writeDensity,
} from "./density";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("readDensity", () => {
  it("defaults to comfortable, as DD-41 sets it", () => {
    expect(readDensity()).toBe("comfortable");
  });

  it("round-trips a stored choice", () => {
    writeDensity("compact");
    expect(readDensity()).toBe("compact");
  });

  it("ignores a stored value that is not a density", () => {
    window.localStorage.setItem(DENSITY_STORAGE_KEY, "cosy");
    expect(readDensity()).toBe("comfortable");
  });

  it("survives storage that throws rather than returning null", () => {
    // A private window and a browser set to block site data both throw on access. theme.ts
    // learned this first; a density preference is not worth a blank page either.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readDensity()).toBe("comfortable");
  });

  it("does not throw when writing to storage that throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => writeDensity("compact")).not.toThrow();
  });
});

describe("resolveDensity", () => {
  it("lets a saved view override the user's preference", () => {
    expect(resolveDensity("compact", "comfortable")).toBe("compact");
    expect(resolveDensity("comfortable", "compact")).toBe("comfortable");
  });

  it("falls back to the user's preference when the view says nothing", () => {
    expect(resolveDensity(undefined, "compact")).toBe("compact");
    expect(resolveDensity(null, "comfortable")).toBe("comfortable");
  });

  it("falls back rather than throwing on a malformed stored value", () => {
    // `saved_views.config` is validated as "a JSON object" and nothing more, so anything at all
    // can be in this key.
    expect(resolveDensity({ nested: true }, "compact")).toBe("compact");
    expect(resolveDensity("cosy", "compact")).toBe("compact");
  });
});

describe("isDensity", () => {
  it("accepts exactly the two presets", () => {
    expect(isDensity("comfortable")).toBe(true);
    expect(isDensity("compact")).toBe(true);
    expect(isDensity("cosy")).toBe(false);
    expect(isDensity(undefined)).toBe(false);
  });
});
