// The bot's `counts` text (#3974, #3986): one number per key, said only when it is above 0.
import { describe, expect, it } from "vitest";

import { httpApi } from "../src/account/api";
import { countOf } from "../src/account/api-names";

describe("countOf", () => {
  it("reads one key from the counts text", () => {
    expect(countOf("shown:1", "shown")).toBe(1);
    expect(countOf("kept:3 shown:2", "shown")).toBe(2);
  });
  it("says nothing for a count of 0, a missing key or a bad number", () => {
    expect(countOf("held:0", "held")).toBeUndefined();
    expect(countOf(undefined, "shown")).toBeUndefined();
    expect(countOf("", "shown")).toBeUndefined();
    expect(countOf("shown:x", "shown")).toBeUndefined();
  });
});

/** A pretend network: each request gets the next answer, so the page sends one yes and reads its answer. */
function fakeApi(answers: unknown[]) {
  const queue = [...answers];
  return httpApi("https://api.example", (async () =>
    new Response(JSON.stringify(queue.shift() ?? {}), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch);
}

describe("answerWaiting reads the bot's counts", () => {
  it("passes shown from the counts text on", async () => {
    await import("../src/account/api-names");
    const api = fakeApi([{ answer: "a1" }, { state: "done", counts: "shown:1" }]);
    expect(await api.answerWaiting!("100.cmp1", { kind: "claim", action: "confirm", id: "c1" })).toEqual({ shown: 1 });
  });
  it("says no held when the count is 0", async () => {
    await import("../src/account/api-names");
    const api = fakeApi([{ answer: "a1" }, { state: "done", counts: "held:0" }]);
    expect(await api.answerWaiting!("100.cmp1", { kind: "claim", action: "confirm", id: "c1" })).toEqual({});
  });
});
