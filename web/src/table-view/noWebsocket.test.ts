/**
 * FR-U10: no websockets. Views refresh on save and on a fixed interval (polling
 * `query_records`/`get_record`), never a persistent socket. Grep-backed per the pattern in
 * `filters/noHardcodedOperators.test.ts`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const srcRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const webRoot = dirname(srcRoot);

function collectSourceFiles(dir: string, excludedDirNames: Set<string>): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry);
    const stats = statSync(fullPath);
    if (stats.isDirectory()) {
      if (!excludedDirNames.has(entry)) files.push(...collectSourceFiles(fullPath, excludedDirNames));
      continue;
    }
    if (/\.(ts|tsx)$/.test(entry)) files.push(fullPath);
  }
  return files;
}

describe("no websocket client dependency or usage (FR-U10)", () => {
  it("declares no websocket-client package in web/package.json", () => {
    const pkg = JSON.parse(readFileSync(join(webRoot, "package.json"), "utf-8")) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
    const names = [
      ...Object.keys(pkg.dependencies ?? {}),
      ...Object.keys(pkg.devDependencies ?? {}),
    ];
    const offenders = names.filter((name) => /socket\.io|^ws$|websocket/i.test(name));
    expect(offenders).toEqual([]);
  });

  it("no source file under web/src calls `new WebSocket(`", () => {
    const thisFile = fileURLToPath(import.meta.url);
    const files = collectSourceFiles(srcRoot, new Set(["node_modules", "dist"])).filter(
      (file) => file !== thisFile,
    );
    expect(files.length).toBeGreaterThan(0);

    const offenders = files.filter((file) => readFileSync(file, "utf-8").includes("new WebSocket("));
    expect(offenders).toEqual([]);
  });
});
