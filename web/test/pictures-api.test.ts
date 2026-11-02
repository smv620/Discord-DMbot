// The API layer of the kept pictures (#3275): the list and its paging, the filters, the game master's actions and
// their limit, the real client's addresses and its refusals. The page itself comes in #3276.
import { describe, expect, it } from "vitest";

import { httpApi, type PictureList } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { PICTURES_LIVE } from "../src/content/pictures-switch";

const REF = "100.cmp1";
const codes = (list: PictureList): string[] => list.pictures.map((p) => p.code);

describe("the pictures list", () => {
  it("lists newest first, three at a time, and before gives the next three", async () => {
    const api = mockApi("table");
    const first = await api.pictures!(REF, {});
    expect(codes(first)).toEqual(["pic-1", "pic-2", "pic-3"]);
    expect(first.more).toBe(true);
    expect(first.pictures[2]!.cursor).toBe("1791002000:3");
    const next = await api.pictures!(REF, { before: first.pictures[2]!.cursor });
    expect(codes(next)).toEqual(["pic-4", "pic-5"]);
    expect(next.more).toBe(false);
  });

  it("session, poster and q filter the list", async () => {
    const api = mockApi("table");
    expect(codes(await api.pictures!(REF, { session: 2 }))).toEqual(["pic-1", "pic-2", "pic-3"]);
    expect(codes(await api.pictures!(REF, { poster: "Cerric" }))).toEqual(["pic-2", "pic-4"]);
    expect(codes(await api.pictures!(REF, { q: "MAP" }))).toEqual(["pic-1"]);
  });

  it("a player's campaign has no usage, isDm false, and mine only on Mira's pictures", async () => {
    const api = mockApi("table");
    const list = await api.pictures!(`${REF}.player`, { session: 2 });
    expect(list.isDm).toBe(false);
    expect(list.usage).toBeNull();
    expect(list.pictures.filter((p) => p.mine).map((p) => p.poster)).toEqual(["Mira", "Mira"]);
  });
});

describe("the game master's actions", () => {
  it("delete removes a picture; make-private and make-public flip isPublic", async () => {
    const api = mockApi("table");
    await api.pictureAction!(REF, "pic-3", "make-public");
    expect((await api.pictures!(REF, { session: 2 })).pictures.find((p) => p.code === "pic-3")!.isPublic).toBe(true);
    await api.pictureAction!(REF, "pic-1", "make-private");
    expect((await api.pictures!(REF, { session: 2 })).pictures.find((p) => p.code === "pic-1")!.isPublic).toBe(false);
    await api.pictureAction!(REF, "pic-1", "delete");
    expect(codes(await api.pictures!(REF, {}))).not.toContain("pic-1");
  });

  it("permanent is refused with permanent-full on the fifth pick, and not-permanent makes room", async () => {
    const api = mockApi("table");
    // pic-2 is already permanent (100 MB). Picks 1, 3 and 4 fill 400 MB; the fifth (pic-5) is refused.
    await api.pictureAction!(REF, "pic-1", "permanent");
    await api.pictureAction!(REF, "pic-3", "permanent");
    await api.pictureAction!(REF, "pic-4", "permanent");
    expect((await api.pictures!(REF, {})).usage!.permanentMb).toBe(400);
    await expect(api.pictureAction!(REF, "pic-5", "permanent")).rejects.toMatchObject({ kind: "permanent-full" });
    await api.pictureAction!(REF, "pic-2", "not-permanent");
    await api.pictureAction!(REF, "pic-5", "permanent");
    expect((await api.pictures!(REF, {})).usage!.permanentMb).toBe(400);
  });

  it("a player cannot set permanent", async () => {
    const api = mockApi("table");
    await expect(api.pictureAction!(`${REF}.player`, "pic-1", "permanent")).rejects.toMatchObject({ kind: "not-allowed" });
  });

  it("an unknown picture is not found", async () => {
    await expect(mockApi("table").pictureAction!(REF, "pic-99", "delete")).rejects.toMatchObject({ kind: "not-found" });
  });
});

describe("the real client", () => {
  it("asks the right addresses, with only the filters that are set, and maps the page's action names", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method ?? "GET"} ${new URL(url).pathname}${new URL(url).search} ${init?.body ?? ""}`);
      if (init?.method === "POST") return new Response(JSON.stringify({ state: "done" }), { status: 200 });
      return new Response(JSON.stringify({ pictures: [], more: false, isDm: true, usage: null, sessions: [], posters: [] }), { status: 200 });
    }) as typeof fetch);
    await api.pictures!(REF, { session: 2, q: "map" });
    await api.pictures!(REF, {});
    await api.pictureAction!(REF, "pic-1", "make-private");
    await api.pictureAction!(REF, "pic-2", "not-permanent");
    expect(seen).toEqual([
      "GET /me/campaigns/100.cmp1/pictures?session=2&q=map ",
      "GET /me/campaigns/100.cmp1/pictures ",
      'POST /me/campaigns/100.cmp1/pictures/pic-1 {"action":"private"}',
      'POST /me/campaigns/100.cmp1/pictures/pic-2 {"action":"unpermanent"}',
    ]);
  });

  it("a 202 waiting answer is done, and a 409 permanent-full keeps its note", async () => {
    const note = "Permanent pictures are full (400 MB). Unmark one to make room.";
    const waiting = httpApi("https://api.example", (async () => new Response(JSON.stringify({ state: "waiting" }), { status: 202 })) as typeof fetch);
    await expect(waiting.pictureAction!(REF, "pic-1", "permanent")).resolves.toBeUndefined();
    const full = httpApi(
      "https://api.example",
      (async () => new Response(JSON.stringify({ detail: "permanent-full", note }), { status: 409 })) as typeof fetch,
    );
    await expect(full.pictureAction!(REF, "pic-1", "permanent")).rejects.toMatchObject({ kind: "permanent-full", note });
  });
});

describe("the switch", () => {
  it("is off", () => {
    expect(PICTURES_LIVE).toBe(false);
  });
});
