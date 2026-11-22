// Which campaigns show first on My Account (#1991, #5037): the one used last first, whether the person runs it or plays
// in it, never by name, and nothing that needs the person hidden behind the button.
import { describe, expect, it } from "vitest";

import { byRecentlyPlayed, foldCampaigns } from "../src/account/campaign-fold";
import type { Campaign } from "../src/account/api";

const make = (id: string, lastPlayedAt: string | null, status: Campaign["status"] = "active"): Campaign => ({
  id,
  name: `Campaign ${id}`,
  serverName: "Test Table",
  lastPlayedAt,
  status,
  role: "owner",
});
const day = (n: number): string => `2026-09-${String(n).padStart(2, "0")}T19:00:00Z`;

describe("campaign fold", () => {
  it("an older API without lastAccessedAt: the last session, then the creation, never the name", () => {
    const made = (id: string, startedAt: string): Campaign => ({ ...make(id, null), startedAt });
    const list = [made("a", day(2)), make("d", day(4)), made("c", day(7)), make("b", day(9))];
    expect(byRecentlyPlayed(list).map((c) => c.id)).toEqual(["b", "c", "d", "a"]);
  });

  it("never-played campaigns order by when they were made, not by name", () => {
    const made = (id: string, lastAccessedAt: string): Campaign => ({ ...make(id, null), lastAccessedAt });
    const list = [made("a-deploy-31", day(1)), made("z-deploy-41", day(20)), made("m-deploy-35", day(9))];
    expect(byRecentlyPlayed(list).map((c) => c.id)).toEqual(["z-deploy-41", "m-deploy-35", "a-deploy-31"]);
  });

  it("an opened campaign moves to the top: lastAccessedAt counts, not only the last session", () => {
    const list = [make("old", day(1)), make("new", day(9))];
    list[0] = { ...list[0]!, lastAccessedAt: day(15) };
    list[1] = { ...list[1]!, lastAccessedAt: day(9) };
    expect(byRecentlyPlayed(list).map((c) => c.id)).toEqual(["old", "new"]);
  });

  it("campaigns you run and ones you only play in are one list, by use", () => {
    const list = [{ ...make("play", day(9)), role: "player" as const }, make("run", day(3))];
    expect(foldCampaigns(list, new Set()).first.map((c) => c.id)).toEqual(["play", "run"]);
  });

  it("sorts the same moment written two ways as equal, and breaks a full tie by id", () => {
    const list = [make("b", "2026-09-01T19:00:00+00:00"), make("a", "2026-09-01T19:00:00Z"), make("c", "2026-09-02T19:00:00Z")];
    list[0] = { ...list[0]!, name: "Same" };
    list[1] = { ...list[1]!, name: "Same" };
    expect(byRecentlyPlayed(list).map((c) => c.id)).toEqual(["c", "a", "b"]);
  });

  it("five or fewer: everything shows and nothing waits", () => {
    const { first, older } = foldCampaigns([make("a", day(1)), make("b", day(2)), make("c", day(3))], new Set());
    expect(first).toHaveLength(3);
    expect(older).toHaveLength(0);
  });

  it("twelve: the newest five, then seven older", () => {
    const list = Array.from({ length: 12 }, (_, i) => make(`c${i}`, day(i + 1)));
    const { first, older } = foldCampaigns(list, new Set());
    expect(first.map((c) => c.id)).toEqual(["c11", "c10", "c9", "c8", "c7"]);
    expect(older).toHaveLength(7);
  });

  it("an old campaign with an open hand-over offer, or a paused one, shows first", () => {
    const list = Array.from({ length: 12 }, (_, i) => make(`c${i}`, day(i + 1)));
    list[0] = make("c0", day(1), "paused");
    const { first, older } = foldCampaigns(list, new Set(["c1"]));
    expect(first.map((c) => c.id)).toEqual(["c11", "c10", "c9", "c8", "c7", "c1", "c0"]);
    expect(older.map((c) => c.id)).not.toContain("c0");
    expect(older.map((c) => c.id)).not.toContain("c1");
    expect(older).toHaveLength(5);
  });

  it("when what needs the person fills the rest, no button is needed", () => {
    const list = [...Array.from({ length: 5 }, (_, i) => make(`c${i}`, day(i + 10))), make("old", day(1), "paused")];
    expect(foldCampaigns(list, new Set()).older).toHaveLength(0);
  });
});
