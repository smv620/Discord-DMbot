/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Accessibility of the Settings controls (#2488): axe-core on the controls at rest and with the
// retention warning open, on a DM's page and a co-DM's.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import CampaignPageView from "../src/account/CampaignPage";
import { mockApi } from "../src/account/mock";
import { campaignPage as w } from "../src/content/campaign-page";

afterEach(cleanup);

async function serious(): Promise<string[]> {
  const result = await axe.run(document.body, {
    // No layout in happy-dom, so contrast is measured from the stylesheet (a11y-contrast.test.ts).
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

const open = async (ref: string) => {
  render(
    <div class="account">
      <h1>Campaign</h1>
      <CampaignPageView api={mockApi("table")} path={`/account/campaigns/${ref}`} />
    </div>,
  );
  return (await screen.findByRole("heading", { level: 2, name: w.groups.settings })).closest("section") as HTMLElement;
};

describe("the Settings controls", () => {
  it("have nothing serious or critical for an owner and a co-DM", async () => {
    await open("100.cmp1");
    expect(await serious()).toEqual([]);
    cleanup();
    await open("100.cmp1.cp-co");
    expect(await serious()).toEqual([]);
  });

  it("have nothing serious or critical with the retention warning open", async () => {
    const section = await open("100.cmp1");
    fireEvent.click(within(section).getByRole("button", { name: "30 days" }));
    await within(section).findByRole("button", { name: w.deleteAfter("30 days") });
    expect(await serious()).toEqual([]);
  });
});
