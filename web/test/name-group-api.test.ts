// The page's side of splitting and grouping a list of names (#3406): the answer calls, the result read, and the
// pretend API that answers like the bot's half does.
import { describe, expect, it } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";

const ref = mockRecaps()[0]!.ref;

describe("names to check: split and group calls (#3406)", () => {
  const over = (status: number, body: unknown, seen: { method: string; url: string; body: string }[] = []) =>
    httpApi("https://api.example.test", (async (url: string, init: RequestInit) => {
      seen.push({ method: String(init.method), url: String(url), body: String(init.body ?? "") });
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch);

  it("nameAnswerResult reads GET names-to-check/<id>/answer and returns it as it is", async () => {
    const seen: { method: string; url: string; body: string }[] = [];
    const body = { state: "done", note: "Split into 3 names.", error: null, batch: 7 };
    const api = over(200, body, seen);
    expect(await api.nameAnswerResult!("100.cmp1", "a".repeat(32))).toEqual(body);
    expect(seen[0]).toMatchObject({ method: "GET" });
    expect(seen[0]!.url).toContain(`/me/recaps/100.cmp1/names-to-check/${"a".repeat(32)}/answer`);
  });

  it("answerNameCheck posts a split, a join_group, a make_group and an undo with their fields in the body", async () => {
    const seen: { method: string; url: string; body: string }[] = [];
    const api = over(200, {}, seen);
    const id = "a".repeat(32);
    await api.answerNameCheck!("100.cmp1", id, { action: "split" });
    await api.answerNameCheck!("100.cmp1", id, { action: "join_group", group: "d".repeat(32) });
    await api.answerNameCheck!("100.cmp1", id, { action: "make_group", name: "The Ashen Circle" });
    await api.answerNameCheck!("100.cmp1", id, { action: "undo", batch: 7 });
    expect(seen.map((s) => s.body)).toEqual([
      JSON.stringify({ action: "split" }),
      JSON.stringify({ action: "join_group", group: "d".repeat(32) }),
      JSON.stringify({ action: "make_group", name: "The Ashen Circle" }),
      JSON.stringify({ action: "undo", batch: 7 }),
    ]);
  });
});

describe("the pretend API for split and group answers (#3406)", () => {
  it("the pretend API: split of a list is done with its note after two looks", async () => {
    const api = mockApi("table");
    const id = "f".repeat(32);
    await api.answerNameCheck!(ref, id, { action: "split" });
    expect((await api.nameAnswerResult!(ref, id)).state).toBe("working");
    expect(await api.nameAnswerResult!(ref, id)).toEqual({ state: "done", note: "Split into 3 names.", error: null, batch: 7 });
  });

  it("the pretend API: split of a name that is not a list fails with not_a_list", async () => {
    const api = mockApi("table");
    const id = "a".repeat(32);
    await api.answerNameCheck!(ref, id, { action: "split" });
    await api.nameAnswerResult!(ref, id);
    expect(await api.nameAnswerResult!(ref, id)).toMatchObject({ state: "failed", error: "not_a_list", batch: null });
  });

  it("the pretend API: make_group named Taken fails with spelling_taken, and undo of batch 99 fails with answered", async () => {
    const api = mockApi("table");
    const id = "1".repeat(32);
    await api.answerNameCheck!(ref, id, { action: "make_group", name: "Taken" });
    await api.nameAnswerResult!(ref, id);
    expect(await api.nameAnswerResult!(ref, id)).toMatchObject({ state: "failed", error: "spelling_taken" });
    const other = "f".repeat(32);
    await api.answerNameCheck!(ref, other, { action: "undo", batch: 99 });
    await api.nameAnswerResult!(ref, other);
    expect(await api.nameAnswerResult!(ref, other)).toMatchObject({ state: "failed", error: "answered", batch: null });
  });

  it("the pretend API: after an undo the answer is gone (not-found)", async () => {
    const api = mockApi("table");
    const id = "f".repeat(32);
    await api.answerNameCheck!(ref, id, { action: "split" });
    await api.answerNameCheck!(ref, id, { action: "undo", batch: 7 });
    const err = await api.nameAnswerResult!(ref, id).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("not-found");
  });

  it("the pretend API: join_group to an unknown group fails with not_found", async () => {
    const api = mockApi("table");
    const id = "a".repeat(32);
    await api.answerNameCheck!(ref, id, { action: "join_group", group: "e".repeat(32) });
    await api.nameAnswerResult!(ref, id);
    expect(await api.nameAnswerResult!(ref, id)).toMatchObject({ state: "failed", error: "not_found", batch: null });
  });

  it("the pretend API: a name with no answer made to it is not found", async () => {
    const api = mockApi("table");
    const err = await api.nameAnswerResult!(ref, "h".repeat(32)).catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("not-found");
  });
});
