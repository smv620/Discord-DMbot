// Discord ids are bigger than 2^53-1, so the web API sends them as strings (#4060, #4066) and the page must
// never turn one into a number: a number would round the last digits and point at someone else.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";

const BIG = "1234567890123456789"; // above 2^53 - 1: Number() would turn it into ...6800

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx|astro)$/.test(name) ? [path] : [];
  });
}

describe("Discord ids stay strings", () => {
  it("the test id really is one a number would damage", () => {
    expect(String(Number(BIG))).not.toBe(BIG);
  });

  it("no source turns an id, guild, user or channel into a number", () => {
    const bad = /(Number|parseInt|parseFloat|BigInt)\(\s*[\w.?!]*(discord|guild|user|channel|member|snowflake)\w*/i;
    const found = sources(join(__dirname, "..", "src")).flatMap((path) =>
      readFileSync(path, "utf8")
        .split("\n")
        .map((line, i) => ({ path, line: i + 1, text: line }))
        .filter((l) => bad.test(l.text)),
    );
    expect(found).toEqual([]);
  });

  it("a campaign ref with a big guild id goes through the pretend API unchanged", async () => {
    const ref = `${BIG}.cmp1`;
    const api = mockApi("table");
    const waiting = await api.webWaiting!(ref);
    expect(JSON.stringify(waiting)).not.toContain("e+");
    expect(ref.split(".")[0]).toBe(BIG);
  });
});
