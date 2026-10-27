/** @jsxImportSource preact */
// @vitest-environment happy-dom
// My Account's campaign cards and section order (#2460, docs/PLAN.md "Where everything is on the
// website"): the name is the link, the facts sit on the card and a missing one is left out, the
// part you play is said in words, a live session shows, and the account-wide House rules, Homebrew
// and Transcripts cards are gone.
import { cleanup, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import type { AccountApi, Campaign } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { text } from "../src/content/account";
import { houseRulesCard, homebrewCard, transcriptsCard } from "../src/content/account-cards";
import { sharingCard } from "../src/content/sharing";

afterEach(cleanup);

function show(api: AccountApi = mockApi("table")) {
  render(<Account api={api} go={vi.fn()} search="" />);
  return api;
}
const card = async (name: string): Promise<HTMLElement> => {
  const link = await screen.findByRole("link", { name });
  return link.closest("li") as HTMLElement;
};

describe("a campaign's card", () => {
  it("makes the name the link and shows the facts it has", async () => {
    show();
    const c = await card("The Brynwater Crossing");
    expect(within(c).getByRole("link", { name: "The Brynwater Crossing" }).getAttribute("href")).toBe(
      "/account/campaigns/100.cmp1",
    );
    for (const fact of ["12 sessions", "42 hours played", "4 players", "27 NPCs"])
      expect(within(c).getByText(fact)).toBeTruthy();
    expect(within(c).getByText(/^Started /)).toBeTruthy();
    expect(within(c).getByText(/^Last played /)).toBeTruthy();
  });

  it("shows 🟢 Listening now only where a session is running", async () => {
    show();
    const live = await card("The Brynwater Crossing");
    expect(within(live).getByText(text.listeningNow)).toBeTruthy();
    const quiet = await card("Shrine of Varrow");
    expect(within(quiet).queryByText(text.listeningNow)).toBeNull();
  });

  it("leaves a missing fact out instead of saying zero", async () => {
    show();
    const c = await card("Shrine of Varrow"); // no NPCs, no start date in the pretend data
    expect(within(c).getByText("3 sessions")).toBeTruthy();
    expect(within(c).queryByText(/NPC/)).toBeNull();
    expect(within(c).queryByText(/^Started /)).toBeNull();
    expect(within(c).queryByText(/\b0 /)).toBeNull();
  });

  it("says a campaign nobody has played yet, with no facts to invent", async () => {
    show();
    const c = await card("Ashen Crown");
    expect(within(c).getByText(text.notPlayed)).toBeTruthy();
    expect(within(c).queryByText(/sessions?$/)).toBeNull();
  });

  it("says your part in words: you run it, you help run it, you play in it", async () => {
    show();
    expect(within(await card("The Brynwater Crossing")).getByText("You run it")).toBeTruthy();
    expect(within(await card("Shrine of Varrow")).getByText("You help run it")).toBeTruthy();
    const mine = await card("The Lantern Road");
    expect(within(mine).getByText("You play in it")).toBeTruthy();
  });

  it("gives a campaign you only play in no DM tools", async () => {
    show();
    const mine = await card("The Lantern Road");
    expect(within(mine).queryByRole("button")).toBeNull(); // no Hand over
  });

  it("reads an older API that sends none of the facts", async () => {
    const api = mockApi("table");
    const real = api.me.bind(api);
    api.me = async () => {
      const got = await real();
      if (got === null) return got;
      return {
        ...got,
        campaigns: got.campaigns.map(
          ({ live: _l, sessions: _s, hoursPlayed: _h, players: _p, npcs: _n, startedAt: _a, ...rest }): Campaign => rest,
        ),
      };
    };
    show(api);
    const c = await card("The Brynwater Crossing");
    expect(within(c).queryByText(/sessions?$/)).toBeNull();
    expect(within(c).getByText(/^Last played /)).toBeTruthy();
  });
});

describe("the sections of My Account", () => {
  it("come in the owner's order, and the account-wide cards are gone", async () => {
    show();
    await screen.findByRole("heading", { level: 2, name: text.campaignsHeading });
    await screen.findByRole("heading", { level: 2, name: text.charactersHeading }); // (loaded after the page)
    const order = screen
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent ?? "");
    const at = (name: string) => order.findIndex((t) => t.includes(name));
    expect(at(text.campaignsHeading)).toBeGreaterThanOrEqual(0);
    expect(at(text.campaignsHeading)).toBeLessThan(at(text.charactersHeading));
    await screen.findByRole("heading", { level: 2, name: sharingCard.heading });
    const withSharing = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent ?? "");
    const place = (name: string) => withSharing.findIndex((t) => t.includes(name));
    expect(place(text.charactersHeading)).toBeLessThan(place(sharingCard.heading));
    expect(place(sharingCard.heading)).toBeLessThan(place(text.planHeading));
    expect(at(text.charactersHeading)).toBeLessThan(at(text.planHeading));
    expect(at(text.planHeading)).toBeLessThan(at(text.myAccountHeading));
    for (const gone of [houseRulesCard.heading, homebrewCard.heading, transcriptsCard.heading])
      expect(order.some((t) => t === gone)).toBe(false);
  });
});

describe("a person who only plays", () => {
  it("gets no Shared rules section, which is for the campaigns you run (#2460)", async () => {
    const api = mockApi("table");
    const real = api.me.bind(api);
    api.me = async () => {
      const got = await real();
      if (got === null) return got;
      return { ...got, campaigns: got.campaigns.filter((c) => c.role === "player") };
    };
    show(api);
    await screen.findByRole("heading", { level: 2, name: text.campaignsHeading });
    expect(screen.queryByRole("heading", { level: 2, name: sharingCard.heading })).toBeNull();
  });

  it("gets no recaps link, which is a DM's list", async () => {
    const api = mockApi("table");
    const real = api.me.bind(api);
    api.me = async () => {
      const got = await real();
      if (got === null) return got;
      return { ...got, campaigns: got.campaigns.filter((c) => c.role === "player") };
    };
    show(api);
    await card("The Lantern Road");
    expect(screen.queryByRole("link", { name: "Read your session recaps" })).toBeNull();
  });
});
