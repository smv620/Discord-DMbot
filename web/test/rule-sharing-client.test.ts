// @vitest-environment happy-dom
// The rule sharing calls (#2783; core web/rule_sharing.py): the real API's addresses and shapes, the choice a press is
// sent as, and the refusals the page turns away at once. The pretend API hides a wrong address, so the real client is
// checked here.
import { describe, expect, it } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

function fake(answers: unknown[], status = 200) {
  const seen: { url: string; method: string; body: unknown }[] = [];
  const queue = [...answers];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push({ url: String(url), method: String(init?.method), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(queue.shift() ?? {}), { status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}

describe("the rule sharing calls", () => {
  it("reads from the campaign's rule-sharing address", async () => {
    const body = { published: null, following: [], here: [], ruleCount: 2, canShare: true };
    const { api, seen } = fake([body]);
    expect(await api.ruleSharing!("100.cmp1")).toEqual(body);
    expect(seen).toEqual([{ url: "https://api.example/me/campaigns/100.cmp1/rule-sharing", method: "GET", body: undefined }]);
  });

  it("sends a choice as the action and its argument, and returns the answer's id", async () => {
    const { api, seen } = fake([{ answer: "a".repeat(32) }]);
    expect(await api.chooseRuleSharing!("100.cmp1", { action: "publish", arg: "Our Rules" })).toBe("a".repeat(32));
    expect(seen).toEqual([
      { url: "https://api.example/me/campaigns/100.cmp1/rule-sharing", method: "POST", body: { action: "publish", arg: "Our Rules" } },
    ]);
  });

  it("looks at one answer at its own address", async () => {
    const { api, seen } = fake([{ state: "done", stage: "done", action: "unfollow", note: "" }]);
    await api.ruleSharingAnswer!("100.cmp1", "b".repeat(32));
    expect(seen[0]?.url).toBe(`https://api.example/me/campaigns/100.cmp1/rule-sharing/answers/${"b".repeat(32)}`);
  });
});

describe("the refusals the website turns away at once", () => {
  it.each([
    [403, "plan", "plan-needed"],
    [409, "no_rules", "no-rules"],
    [400, "bad_arg", "bad-choice"],
    [429, "too_soon", "too-soon"],
    [403, "not_allowed", "not-allowed"],
  ])("reads %s %s as the %s kind", async (status, code, kind) => {
    const { api } = fake([{ error: code }], status);
    await expect(api.chooseRuleSharing!("100.cmp1", { action: "unfollow" })).rejects.toMatchObject({ kind });
    expect(ApiError).toBeDefined();
  });
});
