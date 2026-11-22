// A campaign's pages note the person's visit once for each page load (#5037): the account page's order follows it. A visit
// that fails never holds the page's own read.
import { describe, expect, it } from "vitest";

import { readCampaign } from "../src/account/CampaignNav";
import { mockApi } from "../src/account/mock";

describe("noting a visit", () => {
  it("reads the campaign and notes one visit, however many navs ask", async () => {
    const api = mockApi("table");
    const ref = "100.cmp1";
    await Promise.all([readCampaign(api, ref), readCampaign(api, ref)]);
    expect(api.calls.filter((c) => c === `visitCampaign:${ref}`)).toHaveLength(1);
    expect(api.calls.filter((c) => c === `campaignPage:${ref}`)).toHaveLength(1);
  });

  it("a visit that fails does not stop the read", async () => {
    const api = mockApi("table");
    api.visitCampaign = () => Promise.reject(new Error("down"));
    const page = await readCampaign(api, "100.cmp1");
    expect(page).not.toBeNull();
  });

  it("an older API without the call still reads the campaign", async () => {
    const api = mockApi("table");
    delete (api as { visitCampaign?: unknown }).visitCampaign;
    expect(await readCampaign(api, "100.cmp1")).not.toBeNull();
  });
});
