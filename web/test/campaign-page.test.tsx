/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A campaign's own page (#2416): the links a DM and a player see, a campaign nobody may open, the
// My Campaigns list linking to it, the recap list narrowed to one campaign, and the way back.
import { cleanup, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/content/import-words", async (original) => ({
  ...(await original<typeof import("../src/content/import-words")>()),
  IMPORT_LIVE: true,
}));

import Account from "../src/account/Account";
import { ApiError, httpApi } from "../src/account/api";
import CampaignNav from "../src/account/CampaignNav";
import CampaignPageView, { campaignRefFromPath } from "../src/account/CampaignPage";
import { campaignHref } from "../src/account/campaignLinks";
import { mockApi } from "../src/account/mock";
import Homebrew from "../src/account/Homebrew";
import HouseRules from "../src/account/HouseRules";
import Import from "../src/account/Import";
import Names from "../src/account/Names";
import Npc from "../src/account/Npc";
import Recaps, { campaignFromSearch } from "../src/account/Recaps";
import Transcripts from "../src/account/Transcripts";
import Timeline from "../src/account/Timeline";
import Web from "../src/account/Web";
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
  const open = async (path: string, api = mockApi("table")) => {
    render(
      <div class="account">
        <h1>Campaign</h1>
        <CampaignPageView api={api} path={path} />
      </div>,
    );
    await screen.findByRole("heading", { level: 2, name: w.groups.story });
  };
  const groups = () => screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);

  it("gives a DM the four groups in order, each page opened on this campaign", async () => {
    await open(DM);
    // The campaign's name is the page's heading and title, so the name is what a screen reader hears.
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("The Brynwater Crossing"));
    expect(document.title).toBe("The Brynwater Crossing · DMbot");
    expect(groups()).toEqual([w.groups.story, w.groups.table, w.groups.rules, w.groups.settings]);
    expect(hrefs()).toEqual(
      expect.arrayContaining([
        "/account/recaps?campaign=100.cmp1",
        "/account/web/100.cmp1",
        "/account/names/100.cmp1",
        "/account/transcripts/100.cmp1",
        "/account/import/100.cmp1", // an import-enabled test server
        "/account/house-rules/100.cmp1", // read by the whole table
        "/account/homebrew/100.cmp1",
        "/account/sharing", // a DM can share rules
      ]),
    );
    // Words equal Discord's and the PLAN's (#2461 item 4).
    for (const label of ["Recaps", "Story timeline", "Story graph", "Names and NPCs", "Transcripts", "Import a past session", "House rules", "Homebrew", "Shared rules"])
      expect(screen.getByRole("link", { name: label }), label).toBeTruthy();
    expect(screen.getByRole("link", { name: w.pages.timeline }).getAttribute("href")).toBe("/account/timeline/100.cmp1");
  });

  it("shows the heading's facts and who you are to the campaign, when the account knows them", async () => {
    await open(DM);
    await screen.findByText("You run it");
    expect(screen.getByLabelText(/The Brynwater Crossing/, { selector: "ul" })).toBeTruthy(); // the facts list
  });

  it("gives the game masters the Settings group: three controls, and where to do the rest in Discord", async () => {
    await open(DM);
    const section = (await screen.findByRole("heading", { level: 2, name: w.groups.settings })).closest("section") as HTMLElement;
    // The three the site can change are buttons with the current choice pressed (#2484).
    for (const [name, value] of [["visibility", "peek"], ["level", "normal"], ["retention", "90"]] as const) {
      const group = within(section).getByRole("group", { name: w.controls[name] });
      for (const [v, text] of w.options[name]!) expect(within(group).getByRole("button", { name: text }).getAttribute("aria-pressed")).toBe(String(v === value));
    }
    expect(within(section).getByText(w.gameMastersCount(2))).toBeTruthy();
    expect(within(section).getByText(w.pausedNo)).toBeTruthy();
    // Game masters, hand over and pause stay in Discord, and the owner sees all of them. Delete is not in the list:
    // it is at the bottom of the page (#2879).
    for (const key of ["gameMasters", "handOver", "pause"] as const)
      expect(within(section).getByText(w.doThisInDiscord(w.paths[key])), key).toBeTruthy();
    for (const key of ["gameMasters", "handOver", "pause"] as const) expect(within(section).getByText(w.settingsLabels[key]), key).toBeTruthy();
    expect(within(section).queryByText(w.doThisInDiscord(w.paths.delete))).toBeNull();
    expect(w.paths.retention).toBe("⚙️ Settings → 🗂️ Campaign management → 🗄️ Transcript retention");
  });

  it("shows a co-DM the controls but not the owner's Discord steps", async () => {
    await open("/account/campaigns/100.cmp3.cp-co");
    const section = (await screen.findByRole("heading", { level: 2, name: w.groups.settings })).closest("section") as HTMLElement;
    expect(within(section).getByRole("button", { name: "🔔 Normal" })).toBeTruthy();
    for (const key of ["handOver", "pause"] as const) expect(within(section).queryByText(w.doThisInDiscord(w.paths[key])), key).toBeNull();
    expect(within(section).getByText(w.ownerOnlyNote)).toBeTruthy();
    expect(within(section).getByText(w.ownerOnlyNote)).toBeTruthy();
  });

  it("says retention is for ever when no days are set, and leaves a missing choice's value out", async () => {
    const api = mockApi("table");
    const read = api.campaignPage!.bind(api);
    api.campaignPage = async (r) => {
      const got = await read(r);
      return { ...got, settings: { ...got.settings!, keepDays: null, paused: true } };
    };
    await open(DM, api);
    const keep = await screen.findByRole("button", { name: w.retentionForever });
    expect(keep.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(w.pausedYes)).toBeTruthy();
    cleanup();
    const old = mockApi("table");
    const real = old.campaignPage!.bind(old);
    old.campaignPage = async (r) => {
      const { settings: _s, ...rest } = await real(r);
      return rest;
    };
    await open(DM, old);
    const section = (await screen.findByRole("heading", { level: 2, name: w.groups.settings })).closest("section") as HTMLElement;
    expect(within(section).queryByRole("button")).toBeNull(); // nothing to show or change without the choices
    expect(within(section).getAllByText(/^Do this in Discord:/).length).toBe(3); // the paths still show
  });

  it("gives a co-DM the same groups", async () => {
    await open("/account/campaigns/100.cmp3.cp-co");
    expect(groups()).toContain(w.groups.settings);
  });

  it("fails closed: an unknown role gets no DM tools and no Settings", async () => {
    const api = mockApi("table");
    const read = api.campaignPage!.bind(api);
    api.campaignPage = async (r) => ({ ...(await read(r)), role: "viewer" as never });
    await open(DM, api);
    expect(groups()).toEqual([w.groups.story, w.groups.table, w.groups.rules]);
    expect(hrefs().some((h) => h?.startsWith("/account/recaps"))).toBe(false);
  });

  it("gives a player only the table's pages and no Settings", async () => {
    await open(PLAYER);
    expect(groups()).toEqual([w.groups.story, w.groups.table, w.groups.rules]);
    expect(screen.queryByRole("heading", { name: w.groups.settings })).toBeNull();
    expect(screen.queryByRole("link", { name: w.groups.settings })).toBeNull(); // not in the tabs either
    const all = hrefs();
    expect(all).toEqual(
      expect.arrayContaining([
        "/account/house-rules/100.cmp1.cp-player",
        "/account/homebrew/100.cmp1.cp-player",
        "/account/transcripts/100.cmp1.cp-player",
        "/account/web/100.cmp1.cp-player",
        "/account/timeline/100.cmp1.cp-player", // for the whole table
      ]),
    );
    for (const dmPage of ["recaps", "names", "import", "sharing"]) {
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
    expect((await screen.findByRole("link", { name: "The Brynwater Crossing" })).getAttribute("href")).toBe(campaignHref(REFS));
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

describe("the breadcrumb and the four tabs on every campaign page (#2461)", () => {
  const REFC = "200000000000000001.cmp1";
  const name = "The Brynwater Crossing";
  const crumbs = () => within(screen.getByRole("navigation", { name: w.crumbsLabel }));
  const tabs = () => within(screen.getByRole("navigation", { name: w.tabsLabel }));
  it.each([
    ["the house rules", () => <HouseRules api={mockApi("table")} path={`/account/house-rules/${REFC}`} />, w.pages.houseRules, w.groups.rules],
    ["the homebrew", () => <Homebrew api={mockApi("table")} path={`/account/homebrew/${REFC}`} />, w.pages.homebrew, w.groups.rules],
    ["the transcripts", () => <Transcripts api={mockApi("table")} path={`/account/transcripts/${REFC}`} />, w.pages.transcripts, w.groups.table],
    ["a transcript", () => <Transcripts api={mockApi("table")} path={`/account/transcripts/${REFC}/1`} />, w.pages.transcripts, w.groups.table],
    ["the timeline", () => <Timeline api={mockApi("table")} path="/account/timeline/100.cmp1" />, w.pages.timeline, w.groups.story],
    ["the story graph", () => <Web api={mockApi("table")} path="/account/web/100.cmp1" />, w.pages.graph, w.groups.story],
    ["the import page", () => <Import api={mockApi("table")} path="/account/import/100.cmp1" />, w.pages.import, w.groups.table],
    ["the names", () => <Names api={mockApi("table")} path="/account/names/100.cmp1" />, w.pages.names, w.groups.story],
    ["an NPC's page", () => <Npc api={mockApi("table")} path="/account/npc/100.cmp1/e1" />, w.pages.names, w.groups.story, true],
    ["a recap", () => <Recaps api={mockApi("table")} path="/account/recaps/200000000000000001.cmp1.1791000000" />, w.pages.recaps, w.groups.story, true],
    ["the recaps of one campaign", () => <Recaps api={mockApi("table")} path="/account/recaps" search={`?campaign=${REFC}`} />, w.pages.recaps, w.groups.story],
  ])("is on %s, with the right tab marked", async (_n, view, page, group, inList = false) => {
    render(view());
    await screen.findByRole("navigation", { name: w.tabsLabel });
    // A page inside a list links the list and names itself last; a list page is the last crumb.
    if (inList) await waitFor(() => expect(crumbs().getByRole("link", { name: page })).toBeTruthy());
    else await waitFor(() => expect(crumbs().getByText(page).getAttribute("aria-current")).toBe("page"));
    await waitFor(() => expect(tabs().getByRole("link", { name: w.groups.settings })).toBeTruthy());
    const marked = tabs().getAllByRole("link").filter((a) => a.getAttribute("aria-current"));
    expect(marked.map((a) => a.textContent)).toEqual([group]);
    expect(crumbs().getByRole("link", { name: w.myAccount }).getAttribute("href")).toBe("/account");
    expect(crumbs().getAllByRole("link").length).toBe(inList ? 3 : 2); // My Account, the campaign and (inside a list) the list; the page itself is not a link
  });

  it("shows a player no Settings tab on any campaign page", async () => {
    render(<CampaignNav api={mockApi("table")} campaignRef="100.cmp1.cp-player" page="transcripts" />);
    await screen.findByRole("navigation", { name: w.tabsLabel });
    await waitFor(() => expect(tabs().getAllByRole("link").length).toBeGreaterThan(0));
    expect(tabs().queryByRole("link", { name: w.groups.settings })).toBeNull();
    expect(tabs().getAllByRole("link").map((a) => a.textContent)).toEqual([w.groups.story, w.groups.table, w.groups.rules]);
  });

  it("marks no tab on the campaign's own page, and names the campaign as the page", async () => {
    render(<CampaignPageView api={mockApi("table")} path={DM} />);
    await screen.findByRole("heading", { level: 2, name: w.groups.story });
    expect(crumbs().getByText(name).getAttribute("aria-current")).toBe("page");
    expect(tabs().getAllByRole("link").some((a) => a.getAttribute("aria-current"))).toBe(false);
  });

  it("asks for the campaign once for two navs, and asks again after a failed read", async () => {
    const api = mockApi("table");
    render(
      <>
        <CampaignNav api={api} campaignRef="100.cmp1" page="transcripts" />
        <CampaignNav api={api} campaignRef="100.cmp1" page="houseRules" />
      </>,
    );
    await waitFor(() => expect(screen.getAllByRole("link", { name: w.groups.settings })).toHaveLength(2));
    expect(api.calls.filter((c) => c === "campaignPage:100.cmp1")).toHaveLength(1);
    cleanup();
    const flaky = mockApi("table");
    const real = flaky.campaignPage!.bind(flaky);
    let first = true;
    flaky.campaignPage = async (r) => {
      if (first) {
        first = false;
        throw new ApiError("network");
      }
      return real(r);
    };
    render(<CampaignNav api={flaky} campaignRef="100.cmp1" page="transcripts" />);
    await screen.findByRole("navigation", { name: w.tabsLabel });
    expect(screen.queryByRole("link", { name: w.groups.settings })).toBeNull(); // role unknown: fail closed
    cleanup();
    render(<CampaignNav api={flaky} campaignRef="100.cmp1" page="transcripts" />);
    expect(await screen.findByRole("link", { name: w.groups.settings })).toBeTruthy(); // the failure was not kept
  });

  it("the old '← campaign' back links are gone", async () => {
    render(<HouseRules api={mockApi("table")} path={`/account/house-rules/${REFC}`} />);
    await screen.findByRole("navigation", { name: w.crumbsLabel });
    expect(screen.queryAllByRole("link", { name: /^← / })).toEqual([]);
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

describe("the breadcrumb on a page inside a list (#2485)", () => {
  it("links the list and names the page last, and marks the tabs as a location", async () => {
    render(<CampaignNav api={mockApi("table")} campaignRef="100.cmp1" page="names" label="Brannoch" />);
    const crumbs = await screen.findByRole("navigation", { name: w.crumbsLabel });
    expect(within(crumbs).getByRole("link", { name: w.pages.names }).getAttribute("href")).toBe("/account/names/100.cmp1");
    expect(within(crumbs).getByText("Brannoch").getAttribute("aria-current")).toBe("page");
    cleanup();
    render(<CampaignNav api={mockApi("table")} campaignRef="100.cmp1" page="recaps" label="The bridge" />);
    const again = await screen.findByRole("navigation", { name: w.crumbsLabel });
    expect(within(again).getByRole("link", { name: w.pages.recaps }).getAttribute("href")).toBe("/account/recaps?campaign=100.cmp1");
    const tabs = await screen.findByRole("link", { name: w.groups.story });
    expect(tabs.getAttribute("aria-current")).toBe("location");
  });

  it("keeps the list as the last crumb when the page is the list itself", async () => {
    render(<CampaignNav api={mockApi("table")} campaignRef="100.cmp1" page="names" />);
    const crumbs = await screen.findByRole("navigation", { name: w.crumbsLabel });
    expect(within(crumbs).queryByRole("link", { name: w.pages.names })).toBeNull();
    expect(within(crumbs).getByText(w.pages.names).getAttribute("aria-current")).toBe("page");
  });
});

describe("Delete this campaign, at the bottom of the page (#2879)", () => {
  // (the page helper above is local to its own block; this one renders the same way)
  const open = async (path: string, api = mockApi("table")) => {
    render(
      <div class="account">
        <h1>Campaign</h1>
        <CampaignPageView api={api} path={path} />
      </div>,
    );
    await screen.findByRole("heading", { level: 2, name: w.groups.story });
  };
  const deleteButton = () => screen.queryByRole("button", { name: /^Delete this campaign: / });

  it("is the last thing on the page for the owner, with the Discord line under it", async () => {
    await open(DM, mockApi("table"));
    await waitFor(() => expect(deleteButton()).toBeTruthy());
    const page = document.querySelector(".campaign-page") as HTMLElement;
    expect(page.lastElementChild?.contains(deleteButton() as HTMLElement)).toBe(true);
    expect(within(page.lastElementChild as HTMLElement).getByText(w.deleteInDiscord(w.paths.delete))).toBeTruthy();
  });

  it("is shown to a co-DM only when the API says they may delete it", async () => {
    const api = mockApi("table");
    await open("/account/campaigns/100.cmp3.cp-co", api);
    expect(deleteButton()).toBeNull();
    cleanup();
    const base = api.me.bind(api);
    api.me = async () => {
      const got = await base();
      return got && { ...got, campaigns: got.campaigns.map((c) => ({ ...c, canDelete: true })) };
    };
    await open("/account/campaigns/100.cmp3.cp-co", api);
    await waitFor(() => expect(deleteButton()).toBeTruthy());
  });

  it("is not shown to a player", async () => {
    await open(PLAYER, mockApi("table"));
    expect(deleteButton()).toBeNull();
  });
});

