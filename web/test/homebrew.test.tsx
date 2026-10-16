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
import { problem, text } from "../src/content/homebrew";

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
      screen.getByText(text.campaignLine("Thursday Table", 3)),
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
    expect(screen.getByRole("link", { name: text.signIn })).toBeTruthy();
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
    expect(kinds).toEqual(["Spells", "Monsters", "Items"]); // not the order the API sent
    expect(screen.getByText("A bolt of ice")).toBeTruthy();
    expect(screen.getByText(/3d6 cold damage/)).toBeTruthy();
  });

  it("says the DM may have more, and where the DM changes them; it is read-only", async () => {
    page(mockApi("table"), `/account/homebrew/${REF}`);
    expect(await screen.findByText(text.leadOneCampaign)).toBeTruthy();
    expect(text.leadOneCampaign).toContain(
      "Your DM may have more that stays behind the DM screen.",
    );
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
            kind: "rule",
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
    ).toEqual(["Rules", "vehicle"]);
  });

  it("says what the file holds, next to the button", async () => {
    page(mockApi("table"), `/account/homebrew/${REF}`);
    expect(await screen.findByText(text.downloadNote)).toBeTruthy();
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
