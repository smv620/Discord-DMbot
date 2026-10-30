// The Story Graph's edits are queued for the bot (202 {answer}) and answered later (#2323,
// #2405): the real client must wait for the answer, not read the 202 as the result. These run the
// real client against a fake server.
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

afterEach(() => vi.useRealTimers());

type Reply = { status: number; body: unknown };

function server(answers: Record<string, Reply[]>) {
  const seen: string[] = [];
  const fetcher = (async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    seen.push(`${init?.method ?? "GET"} ${path}`);
    const queue = answers[`${init?.method ?? "GET"} ${path}`] ?? [{ status: 404, body: { error: "not_found" } }];
    const reply = (queue.length > 1 ? queue.shift() : queue[0]) as Reply;
    return new Response(JSON.stringify(reply.body), { status: reply.status });
  }) as typeof fetch;
  return { seen, api: httpApi("https://api.example", fetcher) };
}

const REF = "5.abc";
const EDGE = { a: "x", b: "y", label: "knows", more: 0, links: [] };

describe("a connection edit", () => {
  it("is queued, then waits for the bot's answer and gives back the edge", async () => {
    vi.useFakeTimers();
    const { api, seen } = server({
      [`POST /me/campaigns/${REF}/web/links/l1`]: [{ status: 202, body: { answer: "a1" } }],
      [`GET /me/campaigns/${REF}/web/answers/a1`]: [
        { status: 200, body: { state: "waiting" } },
        { status: 200, body: { state: "done", edge: EDGE, removed: false } },
      ],
    });
    const done = api.changeWebLink!(REF, { id: "l1", action: "pin", pinned: true } as never);
    await vi.advanceTimersByTimeAsync(3000);
    expect(await done).toEqual(EDGE);
    expect(seen.filter((s) => s.startsWith("GET")).length).toBe(2);
  });

  it("says removed when the answer has no edge, and refuses with the bot's word when it fails", async () => {
    vi.useFakeTimers();
    const gone = server({
      [`POST /me/campaigns/${REF}/web/links/l1`]: [{ status: 202, body: { answer: "a1" } }],
      [`GET /me/campaigns/${REF}/web/answers/a1`]: [{ status: 200, body: { state: "done", edge: null, removed: true } }],
    });
    const removed = gone.api.changeWebLink!(REF, { id: "l1", action: "remove", confirm: true } as never);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await removed).toEqual({ removed: true });
    const refused = server({
      [`POST /me/campaigns/${REF}/web/links`]: [{ status: 202, body: { answer: "a2" } }],
      [`GET /me/campaigns/${REF}/web/answers/a2`]: [{ status: 200, body: { state: "failed", error: "not_found" } }],
    });
    const failing = refused.api.changeWebLink!(REF, { action: "add", a: "x", b: "y", words: "knows", secret: false } as never);
    const caught = failing.catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await caught).toBeInstanceOf(ApiError);
  });
});

describe("a reveal", () => {
  it("is queued and a name's answer is read at the All names answers", async () => {
    vi.useFakeTimers();
    const { api, seen } = server({
      [`POST /me/campaigns/${REF}/web/reveal`]: [{ status: 202, body: { answer: "r1" } }],
      [`GET /me/campaigns/${REF}/all-names/answers/r1`]: [{ status: 200, body: { state: "done", row: null } }],
    });
    const done = api.webReveal!(REF, { kind: "name", id: "n1" });
    await vi.advanceTimersByTimeAsync(1000);
    await done;
    expect(seen).toEqual([`POST /me/campaigns/${REF}/web/reveal`, `GET /me/campaigns/${REF}/all-names/answers/r1`]);
  });

  it("reads a connection's answer at the web's answers, and gives up with still-waiting", async () => {
    vi.useFakeTimers();
    const { api, seen } = server({
      [`POST /me/campaigns/${REF}/web/reveal`]: [{ status: 202, body: { answer: "r2" } }],
      [`GET /me/campaigns/${REF}/web/answers/r2`]: [{ status: 200, body: { state: "waiting" } }],
    });
    const slow = api.webReveal!(REF, { kind: "link", id: "l1" });
    const caught = slow.catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(70_000);
    const error = await caught;
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("still-waiting");
    expect(seen[1]).toBe(`GET /me/campaigns/${REF}/web/answers/r2`);
  });
});
