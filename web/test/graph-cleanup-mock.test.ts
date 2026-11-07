// The pretend API's graph clean-up data (#3762, under #3740): the shapes the clean-up page is tested against.
import { describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";

describe("the pretend graph clean-up (#3762)", () => {
  it("answers the DM with three groups, largest first, sized 3, 2 and 1", async () => {
    const api = mockApi("table");
    const view = await api.graphCleanup!("100.cmp1.cleanup");
    expect(view.groups.map((g) => g.entries.length)).toEqual([3, 2, 1]);
    expect(view.total).toBe(6);
    expect(view.shown).toBe(6);
    expect(view.noPlayerCharacter).toBe(false);
    expect(view.review).toEqual({ state: "waiting", minutesLeft: 12, fixes: 2 });
  });

  it("keeps every link inside its own group", async () => {
    const api = mockApi("table");
    const view = await api.graphCleanup!("100.cmp1.cleanup");
    for (const group of view.groups) {
      const ids = new Set(group.entries.map((e) => e.id));
      for (const link of group.links) {
        expect(ids.has(link.a)).toBe(true);
        expect(ids.has(link.b)).toBe(true);
      }
    }
  });

  it("gives the unnamed people their unnamed fields", async () => {
    const api = mockApi("table");
    const view = await api.graphCleanup!("100.cmp1.cleanup");
    const all = view.groups.flatMap((g) => g.entries);
    const child = all.find((e) => e.id === "e2");
    expect(child).toMatchObject({ unnamed: true, unnamedWhat: "child", unnamedAnchor: "Okthandom", status: "waiting" });
    const cousin = all.find((e) => e.id === "e6");
    expect(cousin).toMatchObject({ unnamed: true, unnamedWhat: "cousin", unnamedAnchor: "The Ferryman", status: "confirmed" });
  });

  it("answers an empty campaign with no groups and an idle review", async () => {
    const view = await mockApi("table").graphCleanup!("100.cmp1.cleanup-empty");
    expect(view).toMatchObject({ groups: [], total: 0, shown: 0, noPlayerCharacter: false, review: { state: "idle" } });
  });

  it("says a campaign with no player character has no groups", async () => {
    const view = await mockApi("table").graphCleanup!("100.cmp1.cleanup-nopc");
    expect(view).toMatchObject({ groups: [], total: 0, shown: 0, noPlayerCharacter: true });
  });

  it("lists 300 entries of 450 in a big campaign", async () => {
    const view = await mockApi("table").graphCleanup!("100.cmp1.cleanup-big");
    expect(view.total).toBe(450);
    expect(view.shown).toBe(300);
    expect(view.groups.reduce((n, g) => n + g.entries.length, 0)).toBe(300);
  });

  it("carries the done and off reviews on the same groups", async () => {
    const api = mockApi("table");
    const done = await api.graphCleanup!("100.cmp1.cleanup-done");
    expect(done.review).toEqual({ state: "done", missed: 2 });
    expect(done.groups.map((g) => g.entries.length)).toEqual([3, 2, 1]);
    const off = await api.graphCleanup!("100.cmp1.cleanup-off");
    expect(off.review).toEqual({ state: "off" });
    expect(off.groups.map((g) => g.entries.length)).toEqual([3, 2, 1]);
  });

  it("refuses a player's view", async () => {
    const api = mockApi("table");
    await expect(api.graphCleanup!("100.cmp1.web-player")).rejects.toBeInstanceOf(ApiError);
    await expect(api.graphCleanup!("100.cmp1.web-player")).rejects.toMatchObject({ kind: "not-allowed" });
  });

  it("records the call", async () => {
    const api = mockApi("table");
    await api.graphCleanup!("100.cmp1.cleanup");
    expect(api.calls).toContain("graphCleanup:100.cmp1.cleanup");
  });
});
