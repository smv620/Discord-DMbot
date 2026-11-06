/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The homebrew pages (#1525): the campaigns, one campaign's shared entries grouped by kind, and the
// download; for anyone in the server, so nothing here is DM-only.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import HomebrewPage, { refFromPath } from "../src/account/Homebrew";
import { mockApi } from "../src/account/mock";
import { kindOrder, problem, text } from "../src/content/homebrew";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";
const page = (api: AccountApi, path: string) =>
  render(<HomebrewPage api={api} path={path} />);

describe("the address", () => {
  it("says which campaign, or the list", () => {
    expect(refFromPath("/account/homebrew")).toBeNull();
    expect(refFromPath("/account/homebrew/")).toBeNull();
    expect(refFromPath(`/account/homebrew/${REF}`)).toBe(REF);
    expect(refFromPath(`/account/homebrew/${REF}/`)).toBe(REF);
  });
});

describe("the campaigns", () => {
  it("lists each campaign with its server and how many entries, and links to it", async () => {
    page(mockApi("table"), "/account/homebrew");
    expect(await screen.findByText("The Brynwater Crossing")).toBeTruthy();
    expect(
      screen.getByText(text.campaignLine("Thursday Table", 5, 1)),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("link", {
          name: text.openCampaign("The Brynwater Crossing"),
        })
        .getAttribute("href"),
    ).toBe(`/account/homebrew/${REF}`);
  });

  it("says plainly when there are none", async () => {
    const api = {
      ...mockApi("table"),
      homebrewCampaigns: async () => [],
    } as AccountApi;
    page(api, "/account/homebrew");
    expect(await screen.findByText(text.empty)).toBeTruthy();
  });

  it("sends a signed-out person to sign in", async () => {
    const api = {
      ...mockApi("table"),
      homebrewCampaigns: async () => {
        throw new ApiError("signed-out");
      },
    } as AccountApi;
    page(api, "/account/homebrew");
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
    // The page's own panel only says it; the one Sign in button is the shared line's (#2301).
    expect(screen.queryByRole("link", { name: text.signIn })).toBeNull();
  });

  it("says it did not load, in plain words, on an API without the routes", async () => {
    const { homebrewCampaigns: _gone, ...older } = mockApi("table");
    page(older as AccountApi, "/account/homebrew");
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      text.failed,
    );
  });
});

describe("followed packs (#1586)", () => {
  it("shows each followed pack under the table's own entries, by kind, with its name and version", async () => {
    page(mockApi("table"), `/account/homebrew/${REF}`);
    expect(await screen.findByText(text.packsHeading)).toBeTruthy();
    expect(screen.getByText(text.packLine("Frost Pack", 2))).toBeTruthy();
    expect(screen.getByText("Ice Lance")).toBeTruthy();
    expect(screen.getByText("Aurora Cloak")).toBeTruthy();
    const body = document.body.textContent ?? "";
    expect(body.indexOf("Mud Wight")).toBeLessThan(body.indexOf("Ice Lance")); // own first
    expect(screen.getByText(text.packsNote)).toBeTruthy();
  });

  it("says so under a pack with no entries", async () => {
    const mock = mockApi("table");
    const api = {
      ...mock,
      homebrew: async (ref: string) => {
        const found = await mock.homebrew!(ref);
        return { ...found, packs: [{ name: "Quiet Pack", version: 1, entries: [] }] };
      },
    } as AccountApi;
    page(api, `/account/homebrew/${REF}`);
    expect(await screen.findByText(text.packLine("Quiet Pack", 1))).toBeTruthy();
    expect(screen.getByText(text.packNone)).toBeTruthy();
  });

  it("says the campaign list follows a pack", async () => {
    page(mockApi("table"), "/account/homebrew");
    expect(
      await screen.findByText("Thursday Table · 5 entries · follows another table's pack"),
    ).toBeTruthy();
  });

  it("says there is none of its own but a pack, with no download", async () => {
    const mock = mockApi("table");
    const api = {
      ...mock,
      homebrew: async (ref: string) => ({ ...(await mock.homebrew!(ref)), entries: [] }),
    } as AccountApi;
    page(api, `/account/homebrew/${REF}`);
    expect(await screen.findByText(text.emptyButPack)).toBeTruthy();
    expect(screen.getByText("Ice Lance")).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.download })).toBeNull();
  });

  it("shows no pack section and the short download note on an older API", async () => {
    const mock = mockApi("table");
    const older = {
      ...mock,
      homebrew: async (ref: string) => {
        const { packs: _gone, ...rest } = await mock.homebrew!(ref);
        return rest;
      },
      homebrewCampaigns: async () =>
        (await mock.homebrewCampaigns!()).map(({ packs: _p, ...rest }) => rest),
    } as AccountApi;
    page(older, `/account/homebrew/${REF}`);
    await screen.findByText("Mud Wight");
    expect(screen.queryByText(text.packsHeading)).toBeNull();
    expect(screen.getByText(text.downloadNote)).toBeTruthy();
    cleanup();
    page(older, "/account/homebrew");
    expect(await screen.findByText("Thursday Table · 5 entries")).toBeTruthy();
  });
});

describe("shared-book tags (#1579)", () => {
  it("tags a shared book's entry, and a 2014 one [Legacy 2014], and says what they mean", async () => {
    page(mockApi("table"), `/account/homebrew/${REF}`);
    await screen.findByText("Mud Wight");
    expect(screen.getAllByText(text.fromBook)).toHaveLength(2); // Moon Lamp and Mud Wight
    expect(screen.getAllByText(text.legacy)).toHaveLength(1); // Mud Wight only
    expect(screen.getByText(text.bookNote)).toBeTruthy();
    expect(text.bookNote).toContain("that book is for the 2014 rules");
  });

  it("shows no tag and no note when nothing is from a book, or on an older API", async () => {
    const mock = mockApi("table");
    const older = {
      ...mock,
      homebrew: async (ref: string) => {
        const found = await mock.homebrew!(ref);
        return {
          ...found,
          entries: found.entries.map(({ fromBook: _a, legacy: _b, ...rest }) => rest),
        };
      },
    } as AccountApi;
    page(older, `/account/homebrew/${REF}`);
    await screen.findByText("Mud Wight");
    expect(screen.queryByText(text.fromBook)).toBeNull();
    expect(screen.queryByText(text.legacy)).toBeNull();
    expect(screen.queryByText(text.bookNote)).toBeNull();
  });
});

describe("a campaign's entries", () => {
  it("groups them by kind in the bot's order, each with its summary and words", async () => {
    page(mockApi("table"), `/account/homebrew/${REF}`);
    expect(
      await screen.findByRole("heading", { name: "The Brynwater Crossing" }),
    ).toBeTruthy();
    const kinds = screen
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(kinds.slice(0, 3)).toEqual(["Spells", "Monsters", "Items"]); // not the API's order
    expect(screen.getByText("A bolt of ice")).toBeTruthy();
    expect(screen.getByText(/3d6 cold damage/)).toBeTruthy();
  });

  it("says what (off) means, and where the DM changes things; it is read-only", async () => {
    page(mockApi("table"), `/account/homebrew/${REF}`);
    expect(await screen.findByText(text.leadOneCampaign)).toBeTruthy();
    expect(text.leadOneCampaign).toContain("An entry marked (off) is kept");
    expect(screen.getByText(text.dmChanges)).toBeTruthy();
    expect(text.dmChanges).toContain("⚙️ Settings → 📚 Homebrew");
    expect(
      screen.queryByRole("button", { name: /add|edit|remove/i }),
    ).toBeNull();
  });

  it("says there are none, with no download, when nothing is shared", async () => {
    const api = {
      ...mockApi("table"),
      homebrew: async () => ({ name: "One", fileName: "x.txt", entries: [] }),
    } as AccountApi;
    page(api, `/account/homebrew/${REF}`);
    expect(await screen.findByText(text.empty)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.download })).toBeNull();
  });

  it("still shows a kind it does not know, after the others", async () => {
    const api = {
      ...mockApi("table"),
      homebrew: async () => ({
        name: "One",
        fileName: "x.txt",
        entries: [
          { kind: "vehicle", name: "Sky Cart", summary: null, text: "Flies." },
          {
            kind: "other",
            name: "Fast Rest",
            summary: null,
            text: "Ten minutes.",
          },
        ],
      }),
    } as AccountApi;
    page(api, `/account/homebrew/${REF}`);
    await screen.findByText("Sky Cart");
    expect(
      screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent),
    ).toEqual(["Other", "vehicle"]);
  });

  it("says where an entry came from, and nothing for the DM's own", async () => {
    const mock = mockApi("table");
    const api = {
      ...mock,
      homebrew: async (ref: string) => {
        const found = await mock.homebrew!(ref);
        return {
          ...found,
          entries: [
            { kind: "item", name: "Linked", summary: null, text: "A.", source: "from dndbeyond.com" },
            { kind: "item", name: "Filed", summary: null, text: "B.", source: "credited to dndbeyond.com" },
            { kind: "item", name: "Named", summary: null, text: "C.", source: "by Sam" },
            { kind: "item", name: "Own", summary: null, text: "D.", source: null },
            { kind: "item", name: "Old", summary: null, text: "E." },
          ],
        };
      },
    } as AccountApi;
    page(api, `/account/homebrew/${REF}`);
    const line = async (name: string) =>
      (await screen.findByText(name)).closest("li")!.textContent ?? "";
    expect(await line("Linked")).toContain("from dndbeyond.com");
    expect(await line("Filed")).toContain("credited to dndbeyond.com");
    expect(await line("Named")).toContain("by Sam");
    for (const own of ["Own", "Old"]) {
      expect(await line(own)).not.toMatch(/from |credited to|by /);
    }
  });

  it("marks a turned-off entry (off), muted, and keeps it in the list", async () => {
    const mock = mockApi("table");
    const api = {
      ...mock,
      homebrew: async (ref: string) => {
        const found = await mock.homebrew!(ref);
        return {
          ...found,
          entries: [
            ...found.entries,
            { kind: "item", name: "Old Bell", summary: null, text: "Rings.", enabled: false },
            { kind: "item", name: "New Bell", summary: null, text: "Chimes.", enabled: true },
          ],
        };
      },
    } as AccountApi;
    page(api, `/account/homebrew/${REF}`);
    const off = (await screen.findByText("Old Bell")).closest("li")!;
    expect(off.textContent).toContain(text.off);
    expect(off.classList.contains("muted")).toBe(true);
    const on = screen.getByText("New Bell").closest("li")!;
    expect(on.textContent).not.toContain(text.off);
    expect(screen.getByText("Frost Lance").closest("li")!.textContent).not.toContain(text.off);
  });

  it("has no Rules kind: made-up rules are House rules, with a link", async () => {
    expect(kindOrder).not.toContain("rule");
    expect(Object.keys(text.kinds)).not.toContain("rule");
    page(mockApi("table"), `/account/homebrew/${REF}`);
    await screen.findByText(text.leadOneCampaign);
    const link = screen.getByRole("link", { name: text.rulesLink });
    expect(link.getAttribute("href")).toBe("/account/house-rules");
  });

  it("points to House rules from the campaign list too", async () => {
    page(mockApi("table"), "/account/homebrew");
    const link = await screen.findByRole("link", { name: text.rulesLink });
    expect(link.getAttribute("href")).toBe("/account/house-rules");
  });

  it("says what the file holds, next to the button", async () => {
    page(mockApi("table"), `/account/homebrew/${REF}`);
    expect(
      await screen.findByText(text.downloadNotePacks),
    ).toBeTruthy();
  });

  it("saves the file under the bot's name for it", async () => {
    const api = mockApi("table");
    const file = vi.spyOn(api as Required<AccountApi>, "homebrewFile");
    const names: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        names.push(this.download);
      });
    page(api, `/account/homebrew/${REF}`);
    fireEvent.click(await screen.findByRole("button", { name: text.download }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(file).toHaveBeenCalledWith(REF);
    expect(names).toEqual(["homebrew-the-brynwater-crossing.txt"]);
    click.mockRestore();
  });

  it("says when saving failed and lets them try again", async () => {
    const api = {
      ...mockApi("table"),
      homebrewFile: vi.fn(async () => {
        throw new ApiError("network");
      }),
    } as AccountApi;
    page(api, `/account/homebrew/${REF}`);
    fireEvent.click(await screen.findByRole("button", { name: text.download }));
    expect(await screen.findByText(text.downloadFailed)).toBeTruthy();
    expect(screen.getByRole("button", { name: text.download })).toBeTruthy();
  });

  it("is not found for a campaign that is not there or not theirs", async () => {
    page(mockApi("table"), "/account/homebrew/999.nope");
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      text.notFound,
    );
    expect(problem("not-found")).toBe(text.notFound);
  });
});

describe("nothing is hidden from players (#1643)", () => {
  it("the homebrew words no longer say the DM lets players know or keeps things behind the screen", async () => {
    const { text: homebrew } = await import("../src/content/homebrew");
    const { entries } = await import("../src/content/whats-new");
    const help = await import("../src/content/help");
    const homebrewHelp = [...help.dmSteps, ...help.playerSteps].filter((st) =>
      /homebrew/i.test(st.text),
    );
    expect(homebrewHelp.length).toBeGreaterThan(0);
    const words = JSON.stringify([
      homebrew,
      entries.filter((e) => /homebrew/i.test(e.title + e.what)),
      homebrewHelp,
    ]);
    expect(words).not.toMatch(/let (the )?players know|lets players know|players may know|marked for players/i);
    expect(words).not.toMatch(/behind the DM screen|shares with players/i);
  });
});
