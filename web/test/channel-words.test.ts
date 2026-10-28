// DMbot no longer has a dmb-rules, dmb-npcs or dmb-gametime channel (owner, 2026-10-28; #2875). No Help, privacy,
// feature, What's new or page text may name them, so the site never points people at a channel that isn't there.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const GONE = /dmb-rules|dmb-npcs|dmb-gametime/i;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : /\.(ts|tsx|astro)$/.test(name) ? [path] : [];
  });
}

describe("channels that are gone", () => {
  it("are named nowhere in the site's words", () => {
    const named = files(join(__dirname, "../src")).filter((path) => GONE.test(readFileSync(path, "utf8")));
    expect(named).toEqual([]);
  });
});
