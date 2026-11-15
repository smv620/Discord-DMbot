// The Maps entry in a campaign's nav (#4347): it appears only while MAPS_LIVE is on, for every campaign page.
import { describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/maps-switch", () => ({
  get MAPS_LIVE() {
    return live.on;
  },
}));

import { sectionPages } from "../src/account/campaignSections";

describe("the Maps entry in the campaign nav", () => {
  it("is absent while the switch is off, and present in the story part while it is on", () => {
    live.on = false;
    expect(sectionPages("100.cmp1", { isDm: true }).story.map((p) => p.key)).not.toContain("maps");
    live.on = true;
    const entry = sectionPages("100.cmp1", { isDm: false }).story.find((p) => p.key === "maps");
    expect(entry?.href).toBe("/account/maps/100.cmp1");
  });
});
