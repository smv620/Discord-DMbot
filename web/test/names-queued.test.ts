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
    const caught = expect(done).rejects.toMatchObject({ kind: "still-waiting" });
    await vi.advanceTimersByTimeAsync(70_000);
    await caught;
  });

  it("asks again one second later, not before", async () => {
    vi.useFakeTimers();
    const { api, seen } = queued([{ state: "waiting" }, { state: "done", row }]);
    const done = api.changeName!("100.cmp1", "n1", { action: "kind", kind: "place" });
    await vi.advanceTimersByTimeAsync(999);
    expect(seen.filter((s) => s.startsWith("GET"))).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(seen.filter((s) => s.startsWith("GET"))).toHaveLength(2);
    await expect(done).resolves.toEqual(row);
  });

  it("asks exactly 60 times over a minute, then says it is still waiting", async () => {
    vi.useFakeTimers();
    const { api, seen } = queued(Array.from({ length: 80 }, () => ({ state: "working" })));
    let result: unknown = "pending";
    const done = api.changeName!("100.cmp1", "n1", { action: "kind", kind: "place" }).then(
      () => (result = "resolved"),
      (e) => (result = e),
    );
    await vi.advanceTimersByTimeAsync(58_999);
    expect(result).toBe("pending");
    await vi.advanceTimersByTimeAsync(1);
    await done;
    expect(result).toMatchObject({ kind: "still-waiting" });
    expect(seen.filter((s) => s.startsWith("GET"))).toHaveLength(60);
  });

  it("keeps asking through a dropped connection and a server error", async () => {
    vi.useFakeTimers();
    let n = 0;
    const fetcher = (async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ answer: "a1" }), { status: 202 });
      n += 1;
      if (n === 1) throw new TypeError("offline");
      if (n === 2) return new Response("{}", { status: 500 });
      if (n === 3) return new Response("{}", { status: 404 });
      return new Response(JSON.stringify({ state: "done", row }), { status: 200 });
    }) as typeof fetch;
    const api = httpApi("https://api.example", fetcher);
    const done = api.changeName!("100.cmp1", "n1", { action: "kind", kind: "place" });
    await vi.advanceTimersByTimeAsync(4000);
    await expect(done).resolves.toEqual(row);
  });

  it("returns the surviving row for a merge", async () => {
    const { api } = queued([{ state: "done", row: { ...row, id: "n2" } }]);
    await expect(api.changeName!("100.cmp1", "n1", { action: "merge", into: "n2" })).resolves.toMatchObject({ id: "n2" });
  });

  it("stops at once when the session has ended", async () => {
    const seen: string[] = [];
    const fetcher = (async (_url: string, init?: RequestInit) => {
      seen.push(init?.method ?? "GET");
      if (init?.method === "POST") return new Response(JSON.stringify({ answer: "a1" }), { status: 202 });
      return new Response("{}", { status: 401 });
    }) as typeof fetch;
    const api = httpApi("https://api.example", fetcher);
    await expect(api.changeName!("100.cmp1", "n1", { action: "kind", kind: "place" })).rejects.toMatchObject({
      kind: "signed-out",
    });
    expect(seen).toEqual(["POST", "GET"]);
  });

  it.each([
    ["bad_target", "bad-target"],
    ["not_found", "not-found"],
    ["needs_confirm", "needs-confirm"],
    ["not_allowed", "not-allowed"],
    ["something_new", "server"],
  ])("turns a failed %s into %s", async (code, kind) => {
    const { api } = queued([{ state: "failed", row: null, error: code }]);
    await expect(api.changeName!("100.cmp1", "n1", { action: "remove", confirm: true })).rejects.toMatchObject({ kind });
  });
});
