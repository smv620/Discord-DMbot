/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The campaign's name is the first heading of every page of a campaign, and the page's own title sits under it (#5038): the
// name is above the page's static title (drawn before any script), which goes one level down, and no heading repeats the name.
import { cleanup, render, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import CampaignNav from "../src/account/CampaignNav";
import HomebrewPage from "../src/account/Homebrew";
import HouseRulesPage from "../src/account/HouseRules";
import Names from "../src/account/Names";
import Transcripts from "../src/account/Transcripts";
import { mockApi } from "../src/account/mock";

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
});

const REF = "200000000000000001.cmp1";

/** A page as the site serves it: `.account` with its static title first and the island's place after it. */
function shell(title: string): HTMLElement {
  document.body.innerHTML = `<div class="account"><h1 id="page-title">${title}</h1><div id="island"></div></div>`;
  return document.getElementById("island")!;
}
const headings = (): { level: string; text: string }[] =>
  Array.from(document.querySelectorAll(".account h1, .account h2")).map((h) => ({ level: h.tagName, text: h.textContent ?? "" }));

describe("the campaign's name on top", () => {
  it("is the first heading, with the page's own title under it", async () => {
    const island = shell("Names");
    render(<CampaignNav api={mockApi("table")} campaignRef={REF} page="names" name="The Brynwater Crossing" />, { container: island });
    await waitFor(() => expect(headings()[0]?.text).toBe("The Brynwater Crossing"));
    expect(headings().slice(0, 2)).toEqual([
      { level: "H1", text: "The Brynwater Crossing" },
      { level: "H2", text: "Names" },
    ]);
    expect(document.getElementById("page-title")?.tagName).toBe("H2"); // (the title keeps its id and its place)
  });

  it("is one heading even when a page has two navs, and keeps its space while the name loads", async () => {
    const island = shell("Names");
    render(
      <>
        <CampaignNav api={mockApi("table")} campaignRef={REF} page="names" />
        <CampaignNav api={mockApi("table")} campaignRef={REF} page="names" />
      </>,
      { container: island },
    );
    await waitFor(() => expect(document.querySelectorAll("[data-campaign-title]")).toHaveLength(1));
  });

  it("is hidden from a screen reader while it has no words, and shown once it has", async () => {
    const island = shell("Names");
    render(<CampaignNav api={mockApi("table")} campaignRef={REF} page="names" />, { container: island });
    const title = (): HTMLElement => document.querySelector("[data-campaign-title]") as HTMLElement;
    await waitFor(() => expect(title()).not.toBeNull());
    await waitFor(() => expect(title().getAttribute("aria-hidden")).toBeNull());
    expect(title().textContent).not.toBe("");
  });

  it("is undone when the last nav leaves: the page's own title is its h1 again", async () => {
    const island = shell("Names");
    const view = render(<CampaignNav api={mockApi("table")} campaignRef={REF} page="names" name="The Brynwater Crossing" />, { container: island });
    await waitFor(() => expect(document.querySelector("[data-campaign-title]")).not.toBeNull());
    view.unmount();
    expect(document.querySelector("[data-campaign-title]")).toBeNull();
    expect(headings()).toEqual([{ level: "H1", text: "Names" }]);
    expect(document.getElementById("page-title")?.hasAttribute("data-page-title")).toBe(false);
  });

  it("leaves the campaign's own page alone (its one heading is already the name)", async () => {
    const island = shell("Campaign");
    render(<CampaignNav api={mockApi("table")} campaignRef={REF} page="campaign" name="The Brynwater Crossing" />, { container: island });
    await new Promise((r) => setTimeout(r, 0));
    expect(document.querySelectorAll("[data-campaign-title]")).toHaveLength(0);
    expect(headings()).toEqual([{ level: "H1", text: "Campaign" }]);
  });

  it("the house rules page has no second heading with the campaign's name", async () => {
    const island = shell("House rules");
    render(<HouseRulesPage api={mockApi("table")} path={`/account/house-rules/${REF}`} />, { container: island });
    await waitFor(() => expect(document.querySelector("[data-campaign-title]")?.textContent).toBe("The Brynwater Crossing"));
    const named = headings().filter((h) => h.text === "The Brynwater Crossing");
    expect(named).toEqual([{ level: "H1", text: "The Brynwater Crossing" }]);
    expect(Array.from(document.querySelectorAll(".account h2, .account h3")).some((h) => h.textContent === "The Brynwater Crossing")).toBe(false);
  });

  // The pages that used to say the campaign's name again under the nav (#5038): one heading with the name, the h1.
  it.each([
    ["Homebrew", (api: ReturnType<typeof mockApi>) => <HomebrewPage api={api} path={`/account/homebrew/${REF}`} />],
    ["Transcripts", (api: ReturnType<typeof mockApi>) => <Transcripts api={api} path={`/account/transcripts/${REF}`} />],
    ["Names", (api: ReturnType<typeof mockApi>) => <Names api={api} path={`/account/names/${REF}`} />],
  ])("%s: the name is the one heading with it, on top", async (title, view) => {
    const island = shell(title);
    render(view(mockApi("table")), { container: island });
    await waitFor(() => expect(document.querySelector("[data-campaign-title]")?.textContent ?? "").not.toBe(""));
    const name = document.querySelector("[data-campaign-title]")!.textContent!;
    expect(headings()[0]).toEqual({ level: "H1", text: name });
    const repeated = Array.from(document.querySelectorAll(".account h2, .account h3")).filter((h) => h.textContent === name);
    expect(repeated).toEqual([]);
  });
});
