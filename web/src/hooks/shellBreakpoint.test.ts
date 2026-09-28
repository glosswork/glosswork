/**
 * The shell breakpoint is written in two places and they must agree.
 *
 * `index.css` declares `--breakpoint-shell` so Tailwind can emit the `shell:` variant; the
 * `useIsWideViewport` hook needs the same number as a `matchMedia` string, and a CSS custom
 * property is not readable by `matchMedia`. The duplication is unavoidable; the drift is not.
 *
 * Reading the stylesheet rather than importing a shared constant is the point: a shared constant
 * would prove the two literals in TypeScript agree while the CSS quietly said something else.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { SHELL_MEDIA_QUERY } from "./useIsWideViewport";

const srcRoot = dirname(dirname(fileURLToPath(import.meta.url)));

describe("the shell breakpoint", () => {
  it("is the same number in index.css and in the media query the hook uses", () => {
    const css = readFileSync(join(srcRoot, "index.css"), "utf8");
    const declared = /--breakpoint-shell:\s*(\d+)px/.exec(css);

    expect(declared, "index.css declares no --breakpoint-shell").not.toBeNull();
    expect(SHELL_MEDIA_QUERY).toBe(`(min-width: ${declared![1]}px)`);
  });

  it("is the 960px docs/DESIGN.md 9 specifies", () => {
    // Pinned to the specified value as well as to itself: the test above passes just as happily
    // if both places move to 800 together, and the number is a decision (docs/DESIGN.md 9), not
    // an implementation detail two files happen to share.
    expect(SHELL_MEDIA_QUERY).toBe("(min-width: 960px)");
  });
});
