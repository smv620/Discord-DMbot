// @vitest-environment happy-dom
// The calls behind "Not named yet" (#2803; core web/all_names.py): the real API's addresses, the three step bodies, and
// how a finished, a questioning and a refused answer come back. The pretend API hides a wrong address, so the real
// client is checked here.
import { describe, expect, it } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

const ID = "a".repeat(32);

function fake(answers: { status?: number; body: unknown }[]) {
  const seen: { url: string; method: string; body: unknown }[] = [];
  const queue = [...answers];
  const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
    seen.push({ url: String(url), method: String(init?.method), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const next = queue.shift() ?? { body: {} };
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);
  return { api, seen };
}

describe("the list", () => {
  it("asks for the people the story never named only when told to", async () => {
    const page = { campaign: "C", canEdit: true, names: [], kinds: [], total: 0, page: 1, pages: 1, pageSize: 50 };
    const { api, seen } = fake([{ body: page }, { body: page }]);
    await api.allNames!("100.cmp1", { q: "", kind: "", sort: "name", page: 1 });
    await api.allNames!("100.cmp1", { q: "", kind: "", sort: "name", page: 1, unnamed: true });
    expect(seen[0]?.url).not.toContain("unnamed");
    expect(seen[1]?.url).toContain("unnamed=1");
  });
});

describe("the name steps", () => {
  it("sends the name to the person's address and returns the answer once it is done", async () => {
    const row = { id: "n1", name: "Mara", kind: "npc", others: [], secret: false, npcId: null };
    const { api, seen } = fake([{ status: 202, body: { answer: ID } }, { body: { state: "done", row, undo: true } }]);
    const got = await api.nameThem!("100.cmp1", "u1", { action: "name", name: "Mara" });
    expect(got).toEqual({ state: "done", row, undo: ID, answer: ID });
    expect(seen[0]).toEqual({ url: "https://api.example/me/campaigns/100.cmp1/all-names/u1", method: "POST", body: { action: "name", name: "Mara" } });
    expect(seen[1]?.url).toBe(`https://api.example/me/campaigns/100.cmp1/all-names/answers/${ID}`);
  });

  it("returns no undo when the bot kept nothing to undo with", async () => {
    const { api } = fake([{ status: 202, body: { answer: ID } }, { body: { state: "done", row: null, undo: false } }]);
    expect(((await api.nameThem!("100.cmp1", "u1", { action: "name-join", into: "n1" })) as { undo: string | null }).undo).toBeNull();
  });

  it("turns a known name into a question, with the entry it is", async () => {
    const other = { id: "n9", name: "Mira Voss" };
    const { api } = fake([{ status: 202, body: { answer: ID } }, { body: { state: "failed", error: "known_name", other } }]);
    expect(await api.nameThem!("100.cmp1", "u1", { action: "name", name: "Mira" })).toEqual({ state: "question", other });
  });

  it("sends the join and the undo with the words core reads", async () => {
    const { api, seen } = fake([
      { status: 202, body: { answer: ID } },
      { body: { state: "done", row: null, undo: true } },
      { status: 202, body: { answer: "b".repeat(32) } },
      { body: { state: "done", row: null, undo: false } },
    ]);
    await api.nameThem!("100.cmp1", "u1", { action: "name-join", into: "n9" });
    await api.nameThem!("100.cmp1", "u1", { action: "name-undo", answer: ID });
    expect(seen[0]?.body).toEqual({ action: "name-join", into: "n9" });
    expect(seen[2]?.body).toEqual({ action: "name-undo", answer: ID });
  });

  it.each([
    ["ambiguous", "ambiguous"],
    ["gone", "gone"],
    ["exists", "name-exists"],
  ])("turns the bot's %s into the page's %s", async (code, kind) => {
    const { api } = fake([{ status: 202, body: { answer: ID } }, { body: { state: "failed", error: code } }]);
    await expect(api.nameThem!("100.cmp1", "u1", { action: "name", name: "Mara" })).rejects.toMatchObject({ kind });
    expect(ApiError).toBeDefined();
  });
});
