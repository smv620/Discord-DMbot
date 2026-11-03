// The Pictures entry in a campaign's nav (#3276): it appears only while PICTURES_LIVE is on, for every campaign page.
import { describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/pictures-switch", () => ({
  get PICTURES_LIVE() {
    return live.on;
  },
}));

import { sectionPages } from "../src/account/campaignSections";

describe("the Pictures entry in the campaign nav", () => {
  it("is absent while the switch is off, and present in the story part while it is on", () => {
    live.on = false;
    expect(sectionPages("100.cmp1", { isDm: true }).story.map((p) => p.key)).not.toContain("pictures");
    live.on = true;
    const entry = sectionPages("100.cmp1", { isDm: false }).story.find((p) => p.key === "pictures");
    expect(entry?.href).toBe("/account/pictures/100.cmp1");
  });
});
