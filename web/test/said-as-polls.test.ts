// @vitest-environment happy-dom
// The real polls behind 🔊 Hear it (#3807; core #3817) and 🎤 Say it (#3828; core #3827), through `httpApi` with a pretend
// network (the pretend API in mock.ts skips this code): a press is queued then polled, an answer comes back, each refusal
// gets the page's word, a bad answer is "server", and a poll that never finishes gives up.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

type Answer = { status: number; body?: unknown };

function fake(answers: Answer[]) {
  const seen: string[] = [];
  const queue = [...answers];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push(`${init?.method} ${String(url).replace("https://api.example", "")}`);
    const next = queue.shift();
    if (!next) return new Response(JSON.stringify({ state: "working" }), { status: 200, headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify(next.body ?? {}), { status: next.status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}
const ID = "a".repeat(32);
const NAME = "b".repeat(32);
const CAMP = "100.cmp1";
const base = `/me/campaigns/${CAMP}/names/${NAME}/said-as`;
const wav = btoa("RIFFxxxxWAVE");

beforeEach(async () => {
  await import("../src/account/api-names");
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

async function kindOf(run: Promise<unknown>): Promise<string> {
  const settled = run.then(() => "resolved", (e: unknown) => (e instanceof ApiError ? e.kind : "other"));
  await vi.advanceTimersByTimeAsync(400_000);
  return settled;
}

describe("🔊 Hear it", () => {
  it("queues the words, polls, and hands back the clip as a Blob", async () => {
    const { api, seen } = fake([
      { status: 200, body: { id: ID } },
      { status: 200, body: { state: "working" } },
      { status: 200, body: { state: "done", audio: wav, mime: "audio/wav" } },
    ]);
    const run = api.hearSaidAs!(CAMP, NAME, "KAH-zeth");
    await vi.advanceTimersByTimeAsync(10_000);
    const blob = await run;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("audio/wav");
    expect(seen).toEqual([`POST ${base}/hear`, `GET ${base}/hear/${ID}`, `GET ${base}/hear/${ID}`]);
  });
  it("says voice-off when the bot reports it", async () => {
    const { api } = fake([{ status: 200, body: { id: ID } }, { status: 200, body: { state: "failed", error: "voice_off" } }]);
    expect(await kindOf(api.hearSaidAs!(CAMP, NAME, "x"))).toBe("voice-off");
  });
  it("says voice-off when the door refuses to queue it", async () => {
    const { api } = fake([{ status: 409, body: { detail: "voice_off" } }]);
    expect(await kindOf(api.hearSaidAs!(CAMP, NAME, "x"))).toBe("voice-off");
  });
  it("says too-soon on a 429", async () => {
    const { api } = fake([{ status: 429, body: { detail: "too_soon" } }]);
    expect(await kindOf(api.hearSaidAs!(CAMP, NAME, "x"))).toBe("too-soon");
  });
  it("is a server problem when it is done but there is no sound", async () => {
    const { api } = fake([{ status: 200, body: { id: ID } }, { status: 200, body: { state: "done" } }]);
    expect(await kindOf(api.hearSaidAs!(CAMP, NAME, "x"))).toBe("server");
  });
  it("is a server problem when the queue gives no id", async () => {
    const { api } = fake([{ status: 200, body: {} }]);
    expect(await kindOf(api.hearSaidAs!(CAMP, NAME, "x"))).toBe("server");
  });
  it("gives up with still-waiting when it never finishes", async () => {
    const { api, seen } = fake([{ status: 200, body: { id: ID } }]);
    expect(await kindOf(api.hearSaidAs!(CAMP, NAME, "x"))).toBe("still-waiting");
    expect(seen.length).toBeGreaterThan(30); // (it kept looking until the limit, not once)
  });
});

describe("🎤 Say it", () => {
  const clip = () => new Blob([new Uint8Array(100)], { type: "audio/webm" });
  it("takes an answer at once (200) without polling", async () => {
    const { api, seen } = fake([{ status: 200, body: { respelling: "KAH-zeth", ipa: "kɑːzɛθ" } }]);
    expect(await api.respellFromVoice!(CAMP, NAME, clip())).toBe("KAH-zeth");
    expect(seen).toEqual([`POST ${base}/from-voice`]);
  });
  it("polls after a 202 and tells the page each look until done", async () => {
    const { api, seen } = fake([
      { status: 202, body: { id: ID } },
      { status: 200, body: { state: "waiting", progress: null } },
      { status: 200, body: { state: "working", progress: null } },
      { status: 200, body: { state: "done", respelling: "KAH-zeth" } },
    ]);
    const states: string[] = [];
    const run = api.respellFromVoice!(CAMP, NAME, clip(), { onLook: (l) => states.push(l.state) });
    await vi.advanceTimersByTimeAsync(20_000);
    expect(await run).toBe("KAH-zeth");
    expect(states).toEqual(["waiting", "working"]);
    expect(seen[1]).toBe(`GET ${base}/from-voice/${ID}`);
  });
  it.each([
    ["voice_off", "voice-off"],
    ["bad_clip", "bad-clip"],
    ["not_allowed", "not-allowed"],
    ["not_found", "not-found"],
    ["failed", "server"],
  ])("maps a failed poll saying %s to %s", async (error, kind) => {
    const { api } = fake([{ status: 202, body: { id: ID } }, { status: 200, body: { state: "failed", error } }]);
    expect(await kindOf(api.respellFromVoice!(CAMP, NAME, clip()))).toBe(kind);
  });
  it("stops at once when the page stops waiting", async () => {
    const { api, seen } = fake([{ status: 202, body: { id: ID } }]);
    let stop = false;
    const run = api.respellFromVoice!(CAMP, NAME, clip(), { cancelled: () => stop });
    await vi.advanceTimersByTimeAsync(2_000);
    stop = true;
    const before = seen.length;
    expect(await kindOf(run)).toBe("server");
    expect(seen.length).toBeLessThanOrEqual(before + 1);
  });
  it("gives up with still-waiting after the limit", async () => {
    const { api } = fake([{ status: 202, body: { id: ID } }]);
    expect(await kindOf(api.respellFromVoice!(CAMP, NAME, clip()))).toBe("still-waiting");
  });
});
