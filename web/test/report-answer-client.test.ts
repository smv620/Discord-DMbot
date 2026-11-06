// @vitest-environment happy-dom
// The report's answer (#2661) is written at once by the API: one POST with the item and the action, no
// polling, and the API's `gone` (409) reaches the page as its own kind.
import { describe, expect, it } from "vitest";

import { httpApi } from "../src/account/api";

function fake(status: number, body: unknown) {
  const seen: { url: string; method: string; body: string }[] = [];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push({ url: String(url), method: String(init?.method), body: String(init?.body ?? "") });
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}

describe("answering a report item", () => {
  it("sends one POST and does not poll", async () => {
    const { api, seen } = fake(200, { answer: { action: "intended", at: 5 } });
    await api.answerReport!("100.cmp1", 1761300000, "a".repeat(32), "intended");
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({
      url: "https://api.example/me/campaigns/100.cmp1/sessions/1761300000/report",
      method: "POST",
    });
    expect(JSON.parse(seen[0]!.body)).toEqual({ id: "a".repeat(32), action: "intended" });
  });

  it("a 409 gone is the page's `gone`, so it reads the report again", async () => {
    const { api } = fake(409, { error: "gone" });
    await expect(api.answerReport!("100.cmp1", 1, "b".repeat(32), "undo")).rejects.toMatchObject({ kind: "gone" });
  });

  it("a player is refused", async () => {
    const { api } = fake(403, { error: "not_allowed" });
    await expect(api.answerReport!("100.cmp1", 1, "b".repeat(32), "remind")).rejects.toMatchObject({
      kind: "not-allowed",
    });
  });
});
