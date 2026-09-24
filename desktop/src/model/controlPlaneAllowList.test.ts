import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Regression guard for the 2026-09-23 "界面显示为空" bug.
 *
 * The Electron main process refuses any control-plane path it has not declared
 * in `allowedPaths` (plus a small set of regex-shaped detail routes). When the
 * API grew new endpoints (observability / experience / topology-evidence …)
 * without the allow-list being updated, every page that consumed them failed
 * with "桌面端拒绝未声明的控制平面接口" and rendered empty metric tiles.
 *
 * This test keeps the two lists in lock-step: every statically declared request
 * path in the renderer (and in main.ts's own call sites) must be reachable.
 */

const here = dirname(fileURLToPath(import.meta.url));
const desktopRoot = join(here, "..", "..");
const mainTsPath = join(desktopRoot, "electron", "main.ts");
const srcDir = join(desktopRoot, "src");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const mainText = readFileSync(mainTsPath, "utf8");

// 1. Literal allow-list entries: the string literals inside `new Set([...])`.
const setBlock = mainText.match(/const allowedPaths = new Set\(\[([\s\S]*?)\]\);/);
if (!setBlock) throw new Error("could not locate the allowedPaths set in electron/main.ts");
const allowedLiterals = new Set(
  [...setBlock[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]),
);

// 2. Regex-shaped detail routes declared in isAllowedControlPlanePath.
const allowedRegexes = [...mainText.matchAll(/\/\^[\s\S]*?\$\/[a-z]*/g)].map((match) => {
  const literal = match[0];
  const lastSlash = literal.lastIndexOf("/");
  return new RegExp(literal.slice(1, lastSlash), literal.slice(lastSlash + 1));
});

function isReachable(path: string): boolean {
  return allowedLiterals.has(path) || allowedRegexes.some((regex) => regex.test(path));
}

// Collect every statically declared request path. Template literals (with
// `${...}`) are intentionally skipped — they are covered by the regex routes.
const requestPattern = /path:\s*"(\/api\/v1\/[^"]*)"/g;
const declared = new Map<string, string>();
for (const file of [...walk(srcDir), mainTsPath]) {
  // For main.ts, drop the allow-list block so the declarations are not counted
  // as requests to themselves.
  const text = file === mainTsPath ? mainText.replace(setBlock[0], "") : readFileSync(file, "utf8");
  for (const match of text.matchAll(requestPattern)) {
    declared.set(match[1], file);
  }
}

describe("desktop control-plane allow-list", () => {
  it("discovers a non-trivial set of declared endpoints", () => {
    expect(declared.size).toBeGreaterThan(20);
    expect(allowedLiterals.size).toBeGreaterThan(20);
  });

  it("declares every statically requested endpoint in the allow-list", () => {
    const missing = [...declared.entries()]
      .filter(([path]) => !isReachable(path))
      .map(([path, file]) => `${path}  (requested by ${file.slice(desktopRoot.length + 1)})`);
    expect(missing).toEqual([]);
  });
});
