/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Shared rules is a page of each campaign (#5039, the build of #5041): a campaign's address keeps its navigation and shows only
// that campaign's sets and packs; the overview links each campaign to its page; a campaign that shares nothing says so.
import { cleanup, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import type { SharedRulesCampaign } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { refFromSharingPath } from "../src/account/Sharing";
import SharingPage from "../src/account/Sharing";
import { text } from "../src/content/sharing";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";

describe("a campaign's Shared rules page", () => {
  it("reads the campaign from the address", () => {
    expect(refFromSharingPath("/account/sharing/200000000000000001.cmp1")).toBe(REF);
    expect(refFromSharingPath("/account/sharing")).toBeNull();
    expect(refFromSharingPath("/account/sharing/")).toBeNull();
    expect(refFromSharingPath("/account/sharing/%E0%A4%A")).toBe("");
  });

  it("keeps the campaign's navigation, and shows only that campaign's sets and packs", async () => {
    const api = mockApi("table");
    const real = api.sharedRules!.bind(api);
    api.sharedRules = async () => {
      const all = await real();
      const other: SharedRulesCampaign = { ...all[0]!, ref: "300000000000000002.cmp2", name: "The Other Table", publishes: [{ kind: "homebrew", name: "Other pack", version: 2, shared: true, hasCode: false }], follows: [] };
      return [...all, other];
    };
    render(<SharingPage api={api} campaignRef={REF} />);
    expect(await screen.findByText("Brynwater table rules")).toBeTruthy();
    expect(screen.queryByText("Other pack")).toBeNull();
    expect(screen.queryByText("The Other Table")).toBeNull();
    // The campaign's navigation is there, with its Rules pages (Shared rules among them).
    expect(screen.getAllByText("Shared rules").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("navigation").length).toBeGreaterThan(0);
  });

  it("a campaign that shares nothing shows the nav and the empty words", async () => {
    render(<SharingPage api={mockApi("table")} campaignRef="999000000000000009.cmp9" />);
    expect(await screen.findByText(text.empty)).toBeTruthy();
  });

  it("the overview links each campaign's heading to its own page", async () => {
    render(<SharingPage api={mockApi("table")} />);
    const heading = await screen.findByRole("heading", { level: 2, name: "The Brynwater Crossing" });
    expect(within(heading).getByRole("link", { name: "The Brynwater Crossing" }).getAttribute("href")).toBe(`/account/sharing/${encodeURIComponent(REF)}`);
  });
});
