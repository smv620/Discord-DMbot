// @vitest-environment happy-dom
// The game masters calls (#2768; core web/game_masters.py): the real API's addresses and shapes, and the queue
// choice a change is sent as. The pretend API hides a wrong address, so the real client is checked here.
import { describe, expect, it } from "vitest";

import { httpApi } from "../src/account/api";

function fake(answers: unknown[]) {
  const seen: { url: string; method: string; body: unknown }[] = [];
  const queue = [...answers];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push({ url: String(url), method: String(init?.method), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(queue.shift() ?? {}), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}

describe("the game masters calls", () => {
  it("reads the list from the campaign's game-masters address", async () => {
    const { api, seen } = fake([{ max: 5, gameMasters: [{ userId: "1", name: "Belleros", plan: true, you: true }] }]);
    const got = await api.campaignGameMasters!("100.cmp1");
    expect(seen).toEqual([{ url: "https://api.example/me/campaigns/100.cmp1/game-masters", method: "GET", body: undefined }]);
    expect(got.gameMasters[0]).toEqual({ userId: "1", name: "Belleros", plan: true, you: true });
  });

  it("searches the people to offer with the typed words encoded, and returns just the people", async () => {
    const { api, seen } = fake([{ people: [{ userId: "2", name: "Dana" }] }]);
    const got = await api.campaignGameMasterCandidates!("100.cmp1", "da na&x");
    expect(seen[0]?.url).toBe("https://api.example/me/campaigns/100.cmp1/game-masters/candidates?q=da%20na%26x");
    expect(got).toEqual([{ userId: "2", name: "Dana" }]);
  });

  it("sends an add or a removal as the queue's co_dm choice with the person's id and which way", async () => {
    const { api, seen } = fake([{ answer: "a".repeat(32) }, { answer: "b".repeat(32) }]);
    await api.setCampaignSetting!("100.cmp1", { setting: "co_dm", value: "2", on: true });
    await api.setCampaignSetting!("100.cmp1", { setting: "co_dm", value: "2", on: false });
    expect(seen.map((s) => [s.method, s.body])).toEqual([
      ["POST", { setting: "co_dm", value: "2", on: true }],
      ["POST", { setting: "co_dm", value: "2", on: false }],
    ]);
  });
});
