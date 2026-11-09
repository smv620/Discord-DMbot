// @vitest-environment happy-dom
// The real poll behind "Make the script" (#3899), through `httpApi` with a pretend network: queue then look until done; a
// refusal ends it at once; a server that cannot be reached is given up on after a few looks, not ten minutes; a failed job says why.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

type Answer = { status: number; body?: unknown };
function fake(answers: Answer[], then: Answer = { status: 200, body: { state: "working" } }) {
  const seen: string[] = [];
  const queue = [...answers];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push(`${init?.method} ${String(url).replace("https://api.example", "")}`);
    const next = queue.shift() ?? then;
    return new Response(JSON.stringify(next.body ?? {}), { status: next.status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}
beforeEach(async () => {
  await import("../src/account/api-campaign");
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());
async function kindOf(run: Promise<unknown>): Promise<string> {
  const settled = run.then(() => "done", (e: unknown) => (e instanceof ApiError ? e.kind : "other"));
  await vi.advanceTimersByTimeAsync(700_000);
  return settled;
}
const ID = "a".repeat(32);
const base = "/me/campaigns/100.cmp1/transcripts/2/script";

describe("makeScript", () => {
  it("queues, looks until done, and tells the page each look", async () => {
    const { api, seen } = fake([
      { status: 200, body: { id: ID } },
      { status: 200, body: { state: "working", progress: null } },
      { status: 200, body: { state: "done" } },
    ]);
    const looks: unknown[] = [];
    const run = api.makeScript!("100.cmp1", 2, (p) => looks.push(p));
    await vi.advanceTimersByTimeAsync(10_000);
    await run;
    expect(seen).toEqual([`POST ${base}`, `GET ${base}/${ID}`, `GET ${base}/${ID}`]);
    expect(looks).toHaveLength(2);
  });
  it.each([
    ["plan", "plan-needed"],
    ["off", "script-off"],
    ["already", "script-already"],
  ])("maps a failed job saying %s to %s", async (error, kind) => {
    const { api } = fake([{ status: 200, body: { id: ID } }, { status: 200, body: { state: "failed", error } }]);
    expect(await kindOf(api.makeScript!("100.cmp1", 2))).toBe(kind);
  });
  it("ends at once on a refusal from a look, and does not wait out ten minutes", async () => {
    const { api, seen } = fake([{ status: 200, body: { id: ID } }, { status: 409, body: { detail: "plan" } }]);
    expect(await kindOf(api.makeScript!("100.cmp1", 2))).toBe("plan-needed");
    expect(seen).toHaveLength(2);
  });
  it("gives up after a few looks when the server cannot be reached", async () => {
    const { api, seen } = fake([{ status: 200, body: { id: ID } }], { status: 503, body: {} });
    expect(await kindOf(api.makeScript!("100.cmp1", 2))).toBe("server");
    expect(seen.length).toBeLessThanOrEqual(7);
  });
  it("gives up with still-waiting after ten minutes of a job that never finishes", async () => {
    const { api } = fake([{ status: 200, body: { id: ID } }]);
    expect(await kindOf(api.makeScript!("100.cmp1", 2))).toBe("still-waiting");
  });
  it("is a server problem when the queue gives no id", async () => {
    const { api } = fake([{ status: 200, body: {} }]);
    expect(await kindOf(api.makeScript!("100.cmp1", 2))).toBe("server");
  });
});
