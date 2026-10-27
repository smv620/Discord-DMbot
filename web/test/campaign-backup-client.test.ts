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
    const set = await run;
    expect(set.files).toHaveLength(1);
    expect(set.files[0]!.blob).toBeInstanceOf(Blob);
    expect(set.files[0]!.name).toBe("campaign.dmbot.json.gz");
    expect(seen.map((c) => `${c.method} ${c.url.replace("https://api.example", "")}`)).toEqual([
      "POST /me/campaigns/100.cmp1/backup",
      `GET /me/campaigns/100.cmp1/backup/${ID}`,
      `GET /me/campaigns/100.cmp1/backup/${ID}`,
      `GET /me/campaigns/100.cmp1/backup/${ID}`,
      `GET /me/campaigns/100.cmp1/backup/${ID}/file`,
    ]);
    expect(heard).toEqual(["waiting", "packing", "done"]);
  });

  it("fetches the main file and every transcripts file, each once, under Discord's names", async () => {
    const ready = progress("done", {
      state: "done", ready: true, fileName: "The-Crossing-2026-10-27.dmbot.json.gz", fileWaiting: true,
      parts: [{ n: 1, size: 10 }, { n: 2, size: 12 }], partsTotal: 2,
    });
    const { api, seen } = fake([
      { status: 202, body: { id: ID } },
      { status: 200, body: ready },
      { status: 200, file: true },
      { status: 200, file: true },
      { status: 200, file: true },
    ]);
    const counted: string[] = [];
    const set = await api.campaignBackup!("100.cmp1", undefined, (done, total) => counted.push(`${done}/${total}`));
    expect(set.files.map((f) => f.name)).toEqual([
      "The-Crossing-2026-10-27.dmbot.json.gz",
      "The-Crossing-2026-10-27.transcripts-1-of-2.dmbot.json.gz",
      "The-Crossing-2026-10-27.transcripts-2-of-2.dmbot.json.gz",
    ]);
    expect(seen.slice(2).map((c) => c.url.replace("https://api.example", ""))).toEqual([
      `/me/campaigns/100.cmp1/backup/${ID}/file`,
      `/me/campaigns/100.cmp1/backup/${ID}/file/1`,
      `/me/campaigns/100.cmp1/backup/${ID}/file/2`,
    ]);
    expect(counted).toEqual(["0/3", "1/3", "2/3", "3/3"]);
  });

  it("does not fetch the main file again when it was already taken", async () => {
    const ready = progress("done", { state: "done", ready: true, fileName: "x.dmbot.json.gz", fileWaiting: false, parts: [{ n: 2, size: 5 }], partsTotal: 2 });
    const { api, seen } = fake([{ status: 202, body: { id: ID } }, { status: 200, body: ready }, { status: 200, file: true }]);
    const set = await api.campaignBackup!("100.cmp1");
    expect(set.files.map((f) => f.name)).toEqual(["x.transcripts-2-of-2.dmbot.json.gz"]);
    expect(seen.at(-1)!.url).toMatch(/\/file\/2$/);
  });

  it("a transcripts file that fails fails the whole backup, and the main file's own refusals stay its own", async () => {
    const ready = progress("done", { state: "done", ready: true, fileName: "x.dmbot.json.gz", parts: [{ n: 1, size: 5 }], partsTotal: 1 });
    const failing = fake([{ status: 202, body: { id: ID } }, { status: 200, body: ready }, { status: 200, file: true }, { status: 404, body: { error: "not_found" } }]);
    await expect(failing.api.campaignBackup!("100.cmp1")).rejects.toMatchObject({ kind: "backup-part" });
    const first = fake([{ status: 202, body: { id: ID } }, { status: 200, body: ready }, { status: 404, body: { error: "not_found" } }]);
    await expect(first.api.campaignBackup!("100.cmp1")).rejects.toMatchObject({ kind: "not-found" });
  });

  it.each([
    ["plan", "no-backup"],
    ["too_big", "too-big"],
    ["too_many_files", "backup-too-many-files"],
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
