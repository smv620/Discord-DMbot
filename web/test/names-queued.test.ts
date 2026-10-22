// @vitest-environment happy-dom
// A name change is queued (202 {answer}) and the page waits for the bot's answer (#2294).
import { afterEach, describe, expect, it, vi } from "vitest";

import { httpApi } from "../src/account/api";

const row = { id: "n1", name: "Mira Voss", kind: "npc", others: [], secret: false, npcId: null };

/** A fake API: the POST answers 202, then each poll gives the next state. */
function queued(states: object[]) {
  const seen: string[] = [];
  const fetcher = (async (url: string, init?: RequestInit) => {
    seen.push(`${init?.method ?? "GET"} ${url.replace("https://api.example", "")}`);
    if (init?.method === "POST") return new Response(JSON.stringify({ answer: "a1" }), { status: 202 });
    return new Response(JSON.stringify(states.shift()), { status: 200 });
  }) as typeof fetch;
  return { api: httpApi("https://api.example", fetcher), seen };
}

afterEach(() => vi.useRealTimers());

describe("changeName", () => {
  it("polls until the bot is done and returns the changed row", async () => {
    vi.useFakeTimers();
    const { api, seen } = queued([{ state: "waiting" }, { state: "working" }, { state: "done", row }]);
    const done = api.changeName!("100.cmp1", "n1", { action: "spell", name: "Mira Voss" });
    await vi.advanceTimersByTimeAsync(3000);
    await expect(done).resolves.toEqual(row);
    expect(seen).toEqual([
      "POST /me/campaigns/100.cmp1/all-names/n1",
      "GET /me/campaigns/100.cmp1/all-names/answers/a1",
      "GET /me/campaigns/100.cmp1/all-names/answers/a1",
      "GET /me/campaigns/100.cmp1/all-names/answers/a1",
    ]);
  });

  it("returns removed after a removal", async () => {
    const { api } = queued([{ state: "done", row: null, removed: true }]);
    await expect(api.changeName!("100.cmp1", "n1", { action: "remove", confirm: true })).resolves.toEqual({
      removed: true,
    });
  });

  it("turns a store-side refusal into the usual error", async () => {
    const { api } = queued([{ state: "failed", row: null, error: "exists" }]);
    await expect(api.changeName!("100.cmp1", "n1", { action: "spell", name: "Gorrak" })).rejects.toMatchObject({
      kind: "name-exists",
    });
  });

  it("gives up with a plain error when the bot never answers", async () => {
    vi.useFakeTimers();
    const { api } = queued(Array.from({ length: 80 }, () => ({ state: "waiting" })));
    const done = api.changeName!("100.cmp1", "n1", { action: "kind", kind: "place" });
    const caught = expect(done).rejects.toMatchObject({ kind: "server" });
    await vi.advanceTimersByTimeAsync(70_000);
    await caught;
  });
});
