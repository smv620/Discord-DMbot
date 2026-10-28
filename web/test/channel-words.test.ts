// DMbot no longer has a dmb-rules, dmb-npcs or dmb-gametime channel (owner, 2026-10-28; #2875). No Help, privacy,
// feature, What's new or page text may name them, so the site never points people at a channel that isn't there.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const GONE = /dmb-rules|dmb-npcs|dmb-gametime/i;
// The players' clock channel is going too (core #2877): its old sentence may stay only behind GAMETIME_IN_CHAT_LIVE.
const OLD_CLOCK = /in (their|your) own channel/;

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

describe("the players' clock sentence", () => {
  it("says the old channel only while the new line is not live", async () => {
    const { GAMETIME_IN_CHAT_LIVE, playerNote } = await import("../src/content/help");
    const { features } = await import("../src/content/features");
    const clock = features.find((f) => f.title === "The game clock")?.what ?? "";
    expect(OLD_CLOCK.test(playerNote)).toBe(!GAMETIME_IN_CHAT_LIVE);
    expect(OLD_CLOCK.test(clock)).toBe(!GAMETIME_IN_CHAT_LIVE);
    expect(playerNote.includes("dmb-chat") && clock.includes("dmb-chat")).toBe(GAMETIME_IN_CHAT_LIVE);
  });
});
