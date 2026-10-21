/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The house-rules pages (#1521): the campaigns, one campaign's rules with the sets it follows and
// the download; for anyone in the server, so nothing here is DM-only.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import HouseRulesPage, { refFromPath } from "../src/account/HouseRules";
import { mockApi } from "../src/account/mock";
import { problem, text } from "../src/content/house-rules";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";
const page = (api: AccountApi, path: string) => render(<HouseRulesPage api={api} path={path} />);

describe("the address", () => {
  it("says which campaign, or the list", () => {
    expect(refFromPath("/account/house-rules")).toBeNull();
    expect(refFromPath("/account/house-rules/")).toBeNull();
    expect(refFromPath(`/account/house-rules/${REF}`)).toBe(REF);
    expect(refFromPath(`/account/house-rules/${REF}/`)).toBe(REF);
  });
});

describe("the campaigns", () => {
  it("lists each campaign with its server and how many rules, and links to it", async () => {
    page(mockApi("table"), "/account/house-rules");
    expect(await screen.findByText("The Brynwater Crossing")).toBeTruthy();
    expect(screen.getByText(text.campaignLine("Thursday Table", 2, 1))).toBeTruthy();
    expect(
      screen.getByRole("link", { name: text.openCampaign("The Brynwater Crossing") }).getAttribute("href"),
    ).toBe(`/account/house-rules/${REF}`);
    expect(screen.getByText(text.lead)).toBeTruthy(); // read-only, and who changes them, here too
    expect(text.lead).toContain("Only your DM can change them");
  });

  it("says plainly when there are none", async () => {
    const api = { ...mockApi("table"), houseRuleCampaigns: async () => [] } as AccountApi;
    page(api, "/account/house-rules");
    expect(await screen.findByText(text.empty)).toBeTruthy();
  });

  it("sends a signed-out person to sign in", async () => {
    const api = {
      ...mockApi("table"),
      houseRuleCampaigns: async () => {
        throw new ApiError("signed-out");
      },
    } as AccountApi;
    page(api, "/account/house-rules");
    expect(await screen.findByText(text.signedOut)).toBeTruthy();
    // The page's own panel only says it; the one Sign in button is the shared line's (#2301).
    expect(screen.queryByRole("link", { name: text.signIn })).toBeNull();
  });

  it("says it did not load, in plain words, on an API without the routes", async () => {
    const { houseRuleCampaigns: _gone, ...older } = mockApi("table");
    page(older as AccountApi, "/account/house-rules");
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", text.failed);
  });
});

describe("a campaign's rules", () => {
  it("lists them numbered as the bot numbers them, newest first, with the \"instead of\"", async () => {
    page(mockApi("table"), `/account/house-rules/${REF}`);
    expect(await screen.findByRole("heading", { name: "The Brynwater Crossing" })).toBeTruthy();
    const items = screen
      .getAllByRole("listitem")
      .map((li) => li.textContent)
      .filter((t) => /^\d+\./.test(t ?? ""));
    expect(items[0]).toContain("2. Potions are a bonus action");
    expect(items[0]).toContain(text.instead("Drinking a potion takes an action"));
    expect(items[1]).toContain("1. Crits double the dice");
    expect(screen.getByText(text.numbersStay)).toBeTruthy();
  });

  it("says that the DM changes them on the DM screen, and who may read them", async () => {
    page(mockApi("table"), `/account/house-rules/${REF}`);
    expect(await screen.findByText(text.leadOneCampaign)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /add|edit|remove/i })).toBeNull(); // read-only
  });

  it("shows a followed set under its own heading, cited as the bot cites it, after the rules", async () => {
    page(mockApi("table"), `/account/house-rules/${REF}`);
    expect(await screen.findByText("Red Table house rules, v4")).toBeTruthy();
    expect(screen.getByText(text.setsHeading)).toBeTruthy();
    expect(screen.getByText(text.setsNote)).toBeTruthy();
    expect(screen.getAllByText(text.setsHeading)).toHaveLength(1); // once, whatever the sets
    expect(screen.getByText("Set rule 1. Inspiration stacks")).toBeTruthy(); // not "1.", so no clash
    const body = document.body.textContent ?? "";
    expect(body.indexOf("Crits double the dice")).toBeLessThan(body.indexOf("Inspiration stacks"));
  });

  it("marks a rule merged in from a set, and shows no set section when there is none", async () => {
    const api = {
      ...mockApi("table"),
      houseRules: async () => ({
        name: "One",
        fileName: "house-rules-one.txt",
        rules: [{ number: 5, rule: "Merged rule", supersedes: null, fromSet: "Red Table house rules, v4" }],
        sets: [],
      }),
    } as AccountApi;
    page(api, `/account/house-rules/${REF}`);
    expect(await screen.findByText("5. Merged rule")).toBeTruthy();
    expect(screen.getByText(text.fromSet("Red Table house rules, v4"))).toBeTruthy();
    expect(screen.queryByText(text.setsHeading)).toBeNull();
  });

  it("says there are none, with no download, when a campaign has none at all", async () => {
    const api = {
      ...mockApi("table"),
      houseRules: async () => ({ name: "One", fileName: "x.txt", rules: [], sets: [] }),
    } as AccountApi;
    page(api, `/account/house-rules/${REF}`);
    expect(await screen.findByText(text.empty)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.download })).toBeNull();
  });

  it("says the table has none of its own but follows a set, when that is so", async () => {
    const mock = mockApi("table");
    const api = {
      ...mock,
      houseRules: async (ref: string) => ({ ...(await mock.houseRules!(ref)), rules: [] }),
    } as AccountApi;
    page(api, `/account/house-rules/${REF}`);
    expect(await screen.findByText(text.emptyButSet)).toBeTruthy();
    expect(screen.getByText("Red Table house rules, v4")).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.download })).toBeNull();
  });

  it("says what the file holds, next to the button", async () => {
    page(mockApi("table"), `/account/house-rules/${REF}`);
    expect(await screen.findByText(text.downloadNote)).toBeTruthy();
  });

  it("saves the file under the bot's name for it", async () => {
    const api = mockApi("table");
    const file = vi.spyOn(api as Required<AccountApi>, "houseRulesFile");
    const names: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        names.push(this.download);
      });
    page(api, `/account/house-rules/${REF}`);
    fireEvent.click(await screen.findByRole("button", { name: text.download }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(file).toHaveBeenCalledWith(REF);
    expect(names).toEqual(["house-rules-the-brynwater-crossing.txt"]);
    click.mockRestore();
  });

  it("says when saving failed and lets them try again", async () => {
    const api = {
      ...mockApi("table"),
      houseRulesFile: vi.fn(async () => {
        throw new ApiError("network");
      }),
    } as AccountApi;
    page(api, `/account/house-rules/${REF}`);
    fireEvent.click(await screen.findByRole("button", { name: text.download }));
    expect(await screen.findByText(text.downloadFailed)).toBeTruthy();
    expect(screen.getByRole("button", { name: text.download })).toBeTruthy();
  });

  it("is not found for a campaign that is not there or not theirs", async () => {
    page(mockApi("table"), "/account/house-rules/999.nope");
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", text.notFound);
    expect(problem("not-found")).toBe(text.notFound);
  });
});
