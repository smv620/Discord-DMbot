// @vitest-environment happy-dom
// The backup offered before Delete (#2751; core #2554): asked for with a POST, polled in the shared progress shape,
// fetched once. The real API's shape, not the old single GET.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { httpApi } from "../src/account/api";

type Answer = { status: number; body?: unknown; file?: boolean };

function fake(answers: Answer[]) {
  const seen: { url: string; method: string }[] = [];
  const queue = [...answers];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push({ url: String(url), method: String(init?.method) });
    const next = queue.shift();
    if (!next) throw new Error("an unexpected call");
    if (next.file) return new Response(new Blob(["{}"]), { status: next.status, headers: { "Content-Type": "application/gzip" } });
    return new Response(JSON.stringify(next.body ?? {}), { status: next.status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}
const progress = (stage: string, extra: object = {}) => ({
  stage, state: "running", startedAt: 100, updatedAt: 100, serverNow: 101, reason: null, ready: false, ...extra,
});
const ID = "a".repeat(32);

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the backup before Delete", () => {
  it("asks with a POST, polls the status, then fetches the file once", async () => {
    const { api, seen } = fake([
      { status: 202, body: { id: ID } },
      { status: 200, body: progress("waiting") },
      { status: 200, body: progress("packing") },
      { status: 200, body: progress("done", { state: "done", ready: true }) },
      { status: 200, file: true },
    ]);
    const heard: string[] = [];
    const run = api.campaignBackup!("100.cmp1", (p) => heard.push(p.stage));
    await vi.advanceTimersByTimeAsync(30_000);
    const blob = await run;
    expect(blob).toBeInstanceOf(Blob);
    expect(seen.map((c) => `${c.method} ${c.url.replace("https://api.example", "")}`)).toEqual([
      "POST /me/campaigns/100.cmp1/backup",
      `GET /me/campaigns/100.cmp1/backup/${ID}`,
      `GET /me/campaigns/100.cmp1/backup/${ID}`,
      `GET /me/campaigns/100.cmp1/backup/${ID}`,
      `GET /me/campaigns/100.cmp1/backup/${ID}/file`,
    ]);
    expect(heard).toEqual(["waiting", "packing", "done"]);
  });

  it.each([
    ["plan", "no-backup"],
    ["too_big", "too-big"],
    ["not_allowed", "not-allowed"],
    ["failed", "backup-failed"],
    ["something else", "backup-failed"],
  ])("a copy DMbot could not make (%s) is the page's %s", async (reason, kind) => {
    const { api } = fake([
      { status: 202, body: { id: ID } },
      { status: 200, body: progress("failed", { state: "failed", reason }) },
    ]);
    await expect(api.campaignBackup!("100.cmp1")).rejects.toMatchObject({ kind });
  });

  it("stops waiting for a copy that never comes", async () => {
    const answers: Answer[] = [{ status: 202, body: { id: ID } }];
    for (let i = 0; i < 400; i++) answers.push({ status: 200, body: progress("reading") });
    const { api } = fake(answers);
    const run = api.campaignBackup!("100.cmp1");
    const caught = run.catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(20 * 60_000);
    expect(await caught).toMatchObject({ kind: "backup-failed" });
  });

  it("too many asks is too-soon, and a campaign that is not theirs is not-found", async () => {
    await expect(fake([{ status: 429, body: { error: "too_soon" } }]).api.campaignBackup!("100.cmp1")).rejects.toMatchObject({ kind: "too-soon" });
    await expect(fake([{ status: 404, body: { error: "not_found" } }]).api.campaignBackup!("100.cmp1")).rejects.toMatchObject({ kind: "not-found" });
  });
});
