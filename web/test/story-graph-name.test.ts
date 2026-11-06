// One name for the page everywhere (#2990): "Story Graph", with a capital S and G. The owner saw "relationship
// web" in some places and "story graph" in others. Comments and code names may say what they like; the words
// people read may not.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const root = resolve(process.cwd(), "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx|astro)$/.test(name) ? [path] : [];
  });
}

/** Each line with its comments taken out (a `//` line, a ` * ` line, or the tail after ` // `). */
function code(path: string): string[] {
  return readFileSync(path, "utf8")
    .split("\n")
    .map((line) => (/^\s*(\/\/|\*|\/\*)/.test(line) ? "" : line.replace(/\s\/\/\s.*$/, "")));
}

describe("the Story Graph has one name", () => {
  it("is never called a relationship web or a lower-case story graph in anything a person reads", () => {
    const wrong = /relationship web|story graph|Story graph/i;
    const bad: string[] = [];
    for (const path of sources(root)) {
      code(path).forEach((line, i) => {
        const hit = line.match(wrong);
        if (hit && hit[0] !== "Story Graph") bad.push(`${path.slice(root.length + 1)}:${i + 1}: ${hit[0]}`);
      });
    }
    expect(bad).toEqual([]);
  });
});
