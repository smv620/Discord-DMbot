/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A campaign's own page (#2416): the links a DM and a player see, a campaign nobody may open, the
// My Campaigns list linking to it, the recap list narrowed to one campaign, and the way back.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { ApiError, httpApi } from "../src/account/api";
import CampaignPageView, { campaignRefFromPath } from "../src/account/CampaignPage";
import { campaignHref } from "../src/account/campaignLinks";
import { mockApi } from "../src/account/mock";
import Names from "../src/account/Names";
import Npc from "../src/account/Npc";
import Recaps, { campaignFromSearch } from "../src/account/Recaps";
import { campaignPage as w } from "../src/content/campaign-page";

afterEach(cleanup);

const DM = "/account/campaigns/100.cmp1";
const PLAYER = "/account/campaigns/100.cmp1.cp-player";
const hrefs = () => screen.getAllByRole("link").map((a) => a.getAttribute("href"));

describe("the address", () => {
  it("reads the campaign out of the path, and builds it back", () => {
    expect(campaignRefFromPath(DM)).toBe("100.cmp1");
    expect(campaignRefFromPath("/account/campaigns/")).toBeNull();
    expect(campaignRefFromPath("/account/names/100.cmp1")).toBeNull();
    expect(campaignHref("5.abc")).toBe("/account/campaigns/5.abc");
  });
});

describe("the page", () => {
  it("gives a DM the DM's tools and the table's pages, each opened on this campaign", async () => {
    render(<CampaignPageView api={mockApi("table")} path={DM} />);
    await screen.findByRole("heading", { name: "The Brynwater Crossing" });
    expect(screen.getByRole("link", { name: w.backToCampaigns }).getAttribute("href")).toBe("/account#campaigns");
    expect(hrefs()).toEqual(
      expect.arrayContaining([
        "/account/recaps?campaign=100.cmp1",
        "/account/house-rules/100.cmp1",
        "/account/homebrew/100.cmp1",
        "/account/names/100.cmp1",
        "/account/import/100.cmp1", // an import-enabled test server
        "/account/transcripts/100.cmp1",
        "/account/web/100.cmp1",
      ]),
    );
    expect(screen.getByRole("heading", { name: w.dmHeading })).toBeTruthy();
  });

  it("gives a co-DM the same tools", async () => {
    render(<CampaignPageView api={mockApi("table")} path="/account/campaigns/100.cmp3.cp-co" />);
    await screen.findByRole("heading", { name: w.dmHeading });
  });

  it("fails closed: an unknown role gets no DM tools", async () => {
    const api = mockApi("table");
    const read = api.campaignPage!.bind(api);
    api.campaignPage = async (r) => ({ ...(await read(r)), role: "viewer" as never });
    render(<CampaignPageView api={api} path={DM} />);
    await screen.findByRole("heading", { name: w.tableHeading });
    expect(screen.queryByRole("heading", { name: w.dmHeading })).toBeNull();
  });

  it("gives a player only the table's pages", async () => {
    render(<CampaignPageView api={mockApi("table")} path={PLAYER} />);
    await screen.findByRole("heading", { name: w.tableHeading });
    expect(screen.queryByRole("heading", { name: w.dmHeading })).toBeNull();
    const all = hrefs();
    expect(all).toEqual(expect.arrayContaining(["/account/transcripts/100.cmp1.cp-player", "/account/web/100.cmp1.cp-player"]));
    for (const dmPage of ["recaps", "house-rules", "homebrew", "names", "import"]) {
      expect(all.some((h) => h?.startsWith(`/account/${dmPage}`))).toBe(false);
    }
  });

  it("says so, with a way back, for a campaign nobody may open", async () => {
    render(<CampaignPageView api={mockApi("table")} path="/account/campaigns/100.cmp1.cp-none" />);
    expect(await screen.findByText(w.notFound)).toBeTruthy();
    expect(screen.getByRole("link", { name: w.backToCampaigns })).toBeTruthy();
  });

  it("offers another try when DMbot can't be reached", async () => {
    const api = mockApi("table");
    api.campaignPage = async () => {
      throw new ApiError("network");
    };
    render(<CampaignPageView api={api} path={DM} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
  });

  it("asks to sign in again when the session ended", async () => {
    const api = mockApi("table");
    api.campaignPage = async () => {
      throw new ApiError("signed-out");
    };
    render(<CampaignPageView api={api} path={DM} />);
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
  });

  it("treats an API without the route as a campaign that isn't there", async () => {
    const api = mockApi("table");
    delete (api as { campaignPage?: unknown }).campaignPage;
    render(<CampaignPageView api={api} path={DM} />);
    expect(await screen.findByText(w.notFound)).toBeTruthy();
  });
});

describe("My Campaigns", () => {
  it("links a campaign whose API sends its address, and only that one", async () => {
    render(<Account api={mockApi("table")} go={vi.fn()} />);
    const brynwater = await screen.findByRole("link", { name: "The Brynwater Crossing" });
    expect(brynwater.getAttribute("href")).toBe("/account/campaigns/100.cmp1");
    expect(brynwater.closest("li")?.getAttribute("data-campaign")).toBe("cmp-brynwater");
  });

  it("leaves a campaign without an address as plain text (an older API)", async () => {
    const api = mockApi("table");
    const read = api.me.bind(api);
    api.me = async () => {
      const me = await read();
      return me && { ...me, campaigns: me.campaigns.map(({ ref: _ref, ...c }) => c) };
    };
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText("The Brynwater Crossing");
    expect(screen.queryByRole("link", { name: "The Brynwater Crossing" })).toBeNull();
  });
});

describe("the recap list", () => {
  const REFS = "200000000000000001.cmp1";
  it("reads the campaign out of the query", () => {
    expect(campaignFromSearch(`?campaign=${REFS}`)).toBe(REFS);
    expect(campaignFromSearch("")).toBeNull();
    expect(campaignFromSearch("?campaign=%3Cb%3E")).toBeNull();
  });

  it("shows one campaign's recaps with a way to all of them and back", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps" search={`?campaign=${REFS}`} />);
    await screen.findByText(w.onlyThisCampaign, { exact: false });
    expect(screen.getAllByRole("link", { name: /Open|open/ }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: w.showAllRecaps }).getAttribute("href")).toBe("/account/recaps");
    expect(screen.getByRole("link", { name: w.backToThis }).getAttribute("href")).toBe(campaignHref(REFS));
  });

  it("says a campaign has none, when none match", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps" search="?campaign=200000000000000001.nothere" />);
    expect(await screen.findByText(w.noneHere)).toBeTruthy();
  });

  it("does not mix up a campaign with one whose id starts the same", async () => {
    const api = mockApi("table");
    const read = api.recaps!.bind(api);
    api.recaps = async () => {
      const all = await read();
      return all.map((r, i) => (i === 0 ? { ...r, ref: "200000000000000001.cmp10.1791000000" } : r));
    };
    render(<Recaps api={api} path="/account/recaps" search={`?campaign=${REFS}`} />);
    await screen.findByText(w.onlyThisCampaign, { exact: false });
    expect(screen.getAllByRole("link", { name: /Open|open/ })).toHaveLength(1);
  });

  it("shows every recap with no campaign asked for", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps" />);
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Open|open/ }).length).toBeGreaterThan(1));
    expect(screen.queryByText(w.onlyThisCampaign, { exact: false })).toBeNull();
  });
});

describe("the way back", () => {
  it("is on a campaign's names, named", async () => {
    render(<Names api={mockApi("table")} path="/account/names/100.cmp1" />);
    const back = await screen.findByRole("link", { name: w.backTo("The Brynwater Crossing") });
    expect(back.getAttribute("href")).toBe("/account/campaigns/100.cmp1");
  });

  it("is on an NPC's page", async () => {
    render(<Npc api={mockApi("table")} path="/account/npc/100.cmp1/e1" />);
    const back = await screen.findByRole("link", { name: w.backToThis });
    expect(back.getAttribute("href")).toBe("/account/campaigns/100.cmp1");
  });

  it("is on a recap's page, named", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps/200000000000000001.cmp1.1791000000" />);
    const back = await waitFor(() => {
      const found = screen.getAllByRole("link").find((a) => a.getAttribute("href") === "/account/campaigns/200000000000000001.cmp1");
      expect(found).toBeTruthy();
      return found as HTMLElement;
    });
    expect(back.textContent).toMatch(/^← /);
  });
});

describe("the real API's call", () => {
  it("asks for the campaign by its encoded address, and turns a refusal into 'not found'", async () => {
    const seen: string[] = [];
    const fetcher = (async (url: string) => {
      seen.push(url.replace("https://api.example", ""));
      return new Response(JSON.stringify({ error: "not_found" }), { status: 404 });
    }) as typeof fetch;
    const api = httpApi("https://api.example", fetcher);
    await expect(api.campaignPage!("5.a/b")).rejects.toMatchObject({ kind: "not-found" });
    expect(seen).toEqual(["/me/campaigns/5.a%2Fb"]);
  });
});

describe("My Campaigns", () => {
  it("keeps the anchor the campaign page's back link goes to", async () => {
    const { container } = render(<Account api={mockApi("table")} go={vi.fn()} />);
    await screen.findByRole("link", { name: "The Brynwater Crossing" });
    expect(container.querySelector("#campaigns")).toBeTruthy();
  });
});
