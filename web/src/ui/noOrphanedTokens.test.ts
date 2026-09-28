/**
 * Every color utility in the tree names a token the theme defines.
 *
 * `index.css` clears the default Tailwind palette (`--color-*: initial`), which is what stops
 * a component reaching an unthemed color. The same line is why a wrong remap is silent: a
 * utility naming a token that does not exist emits NO CSS at all, so the element renders
 * unstyled, nothing errors, and nothing is red. DD-41 removed nineteen of the twenty-three
 * tokens the tree used, across 182 references in 43 files, and the change that did it
 * re-recorded all 35 visual baselines in the same pass — so an unstyled element would have
 * been baked into the baseline every later change is measured against.
 *
 * Grep-backed, in the pattern of `filters/noHardcodedOperators.test.ts` and
 * `access/hidingIsNeverTheOnlySignal.test.ts`.
 *
 * The keyword lists below are Tailwind's own non-color values for each prefix. They fail
 * CLOSED: an unrecognised suffix is reported rather than ignored, so a new Tailwind keyword
 * shows up here as a failing test and gets added deliberately, which is the direction of
 * error this file exists to have.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const srcRoot = dirname(dirname(fileURLToPath(import.meta.url)));

/** Prefixes whose value is a color in this codebase. */
const COLOR_PREFIXES = [
  "bg",
  "text",
  "border",
  "ring",
  "outline",
  "divide",
  "fill",
  "stroke",
  "placeholder",
  "caret",
  "decoration",
  "from",
  "via",
  "to",
] as const;

/** Tailwind values for those prefixes that are not colors. */
const NON_COLOR: Record<string, ReadonlySet<string>> = {
  bg: new Set(["transparent", "current", "inherit", "none", "clip", "origin", "fixed", "local", "scroll", "center", "cover", "contain", "repeat", "no"]),
  text: new Set([
    "2xs", "xs", "sm", "base", "lg", "xl", "2xl", "3xl", "4xl",
    "left", "center", "right", "justify", "start", "end",
    "wrap", "nowrap", "balance", "pretty", "ellipsis", "clip",
    "transparent", "current", "inherit",
  ]),
  border: new Set(["0", "2", "4", "8", "t", "b", "l", "r", "x", "y", "s", "e", "solid", "dashed", "dotted", "double", "hidden", "none", "collapse", "separate", "spacing", "transparent", "current", "inherit"]),
  ring: new Set(["0", "1", "2", "4", "8", "inset", "offset", "transparent", "current", "inherit"]),
  outline: new Set(["0", "1", "2", "4", "8", "none", "solid", "dashed", "dotted", "double", "hidden", "offset", "transparent", "current", "inherit"]),
  divide: new Set(["0", "2", "4", "8", "x", "y", "solid", "dashed", "dotted", "double", "none", "reverse", "transparent", "current", "inherit"]),
  fill: new Set(["none", "current", "transparent", "inherit"]),
  stroke: new Set(["0", "1", "2", "none", "current", "transparent", "inherit"]),
  placeholder: new Set(["transparent", "current", "inherit"]),
  caret: new Set(["transparent", "current", "inherit"]),
  decoration: new Set(["0", "1", "2", "4", "8", "solid", "dashed", "dotted", "double", "wavy", "none", "auto", "from", "slice", "clone", "transparent", "current", "inherit"]),
  from: new Set(["0", "5", "10", "transparent", "current", "inherit"]),
  via: new Set(["0", "5", "10", "transparent", "current", "inherit"]),
  to: new Set(["0", "5", "10", "transparent", "current", "inherit"]),
};

/** Only these may precede a color in a compound utility (`border-t-line`). */
const SIDES = new Set(["t", "b", "l", "r", "x", "y", "s", "e"]);

function definedColorTokens(): Set<string> {
  const css = readFileSync(join(srcRoot, "index.css"), "utf-8");
  return new Set([...css.matchAll(/--color-([a-z0-9-]+):/g)].map((m) => m[1]));
}

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry !== "node_modules" && entry !== "dist") out.push(...sourceFiles(full));
      continue;
    }
    if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/** `hover:bg-raised`, `dark:text-ink-2`, `-mt-1` and arbitrary values all have to survive this. */
const UTILITY = new RegExp(
  String.raw`(?:^|[\s"'\`:])(?:!)?(${COLOR_PREFIXES.join("|")})-([a-z][a-z0-9-]*)\b`,
  "g",
);

describe("DD-41: every color utility names a token the theme defines", () => {
  const tokens = definedColorTokens();

  it("defines the tokens the specification lists", () => {
    // A spot check that the parse worked at all: a test that silently reads zero tokens
    // would pass the main assertion vacuously.
    expect(tokens.has("human")).toBe(true);
    expect(tokens.has("agent")).toBe(true);
    expect(tokens.size).toBeGreaterThan(20);
  });

  it("has no utility naming a token that does not exist", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(srcRoot)) {
      const source = readFileSync(file, "utf-8");
      for (const match of source.matchAll(UTILITY)) {
        const [, prefix, value] = match;
        if (tokens.has(value)) continue;
        if (NON_COLOR[prefix]?.has(value)) continue;
        // A compound Tailwind value like `border-t-line` reads as prefix `border`, value
        // `t-line`. Strip the side or axis and re-check -- but ONLY those, and only as the
        // first segment, and the tail must then be either a token or one of that prefix's
        // own non-color values (`border-l-2` is a width, not an orphan). A looser tail rule
        // quietly accepts `border-bad-line`, because
        // its tail is `line`, which exists: that is precisely the orphan this file is for.
        const [head, ...rest] = value.split("-");
        const tail = rest.join("-");
        if (SIDES.has(head) && tail && (tokens.has(tail) || NON_COLOR[prefix]?.has(tail))) continue;
        offenders.push(`${relative(srcRoot, file).split(sep).join("/")}: ${prefix}-${value}`);
      }
    }
    expect([...new Set(offenders)].sort()).toEqual([]);
  });
});
