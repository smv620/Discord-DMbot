// Which campaigns show first on My Account (#1991): newest first, never-played last, and nothing
// that needs the person hidden behind the button.
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
  it("orders newest first, and the never-played after the played, by name", () => {
    const list = [make("d", null), make("a", day(3)), make("c", null), make("b", day(9))];
    expect(byRecentlyPlayed(list).map((c) => c.id)).toEqual(["b", "a", "c", "d"]);
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
