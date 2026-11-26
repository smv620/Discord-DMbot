/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Every page a DM reaches from a campaign's tabs keeps the campaign's navigation (#5039, the walk Supervisor asked for on
// #5041). This walks every page `sectionPages` returns for a DM (story, add and rules, with the switches on and importsOn),
// renders each on the mock, and checks that its navigation marks that page as the current one. A page with no component
// listed here fails the test, so a new page cannot be skipped.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountApi } from "../src/account/api";
import { sectionPages, type SectionPage } from "../src/account/campaignSections";
import Import from "../src/account/Import";
import Lore from "../src/account/Lore";
import Maps from "../src/account/Maps";
import Names from "../src/account/Names";
import Pictures from "../src/account/Pictures";
import Recaps from "../src/account/Recaps";
import RecapStyleView from "../src/account/RecapStyle";
import HouseRulesPage from "../src/account/HouseRules";
import HomebrewPageView from "../src/account/Homebrew";
import { mockApi } from "../src/account/mock";
import SharingPage, { refFromSharingPath } from "../src/account/Sharing";
import TimelinePage from "../src/account/Timeline";
import Transcripts from "../src/account/Transcripts";
import Web from "../src/account/Web";
import { campaignPage as words } from "../src/content/campaign-page";

// The switches are on for the walk, so every page a DM can reach is in the list.
vi.mock("../src/content/recap-style-words", async (original) => ({
  ...(await original<typeof import("../src/content/recap-style-words")>()),
  get RECAP_STYLE_LIVE() {
    return true;
  },
}));
vi.mock("../src/content/import-words", async (original) => ({
  ...(await original<typeof import("../src/content/import-words")>()),
  get IMPORT_LIVE() {
    return true;
  },
}));
vi.mock("../src/content/lore-words", async (original) => ({
  ...(await original<typeof import("../src/content/lore-words")>()),
  get LORE_LIVE() {
    return true;
  },
}));
vi.mock("../src/content/recap-style-switch", () => ({ RECAP_STYLE_LIVE: true }));
vi.mock("../src/content/import-switch", () => ({ IMPORT_LIVE: true }));
vi.mock("../src/content/lore-switch", () => ({ LORE_LIVE: true }));
vi.mock("../src/content/maps-switch", () => ({ MAPS_LIVE: true }));
vi.mock("../src/content/pictures-switch", () => ({ PICTURES_LIVE: true }));

afterEach(cleanup);

const REF = "200000000000000001.cmp1";

/** The component for each page's address, by its path (the query is read by the page). */
const pageAt = (api: AccountApi, href: string): preact.JSX.Element | null => {
  const path = href.split("?")[0]!;
  const search = href.includes("?") ? `?${href.split("?")[1]}` : "";
  const table: [string, () => preact.JSX.Element][] = [
    ["/account/recaps", () => <Recaps api={api} path="/account/recaps" search={search} />],
    ["/account/recap-style/", () => <RecapStyleView api={api} path={path} />],
    ["/account/timeline/", () => <TimelinePage api={api} path={path} />],
    ["/account/web/", () => <Web api={api} path={path} />],
    ["/account/names/", () => <Names api={api} path={path} />],
    ["/account/transcripts/", () => <Transcripts api={api} path={path} />],
    ["/account/pictures/", () => <Pictures api={api} path={path} />],
    ["/account/maps/", () => <Maps api={api} refId={REF} />],
    ["/account/lore/", () => <Lore api={api} path={path} />],
    ["/account/import/", () => <Import api={api} path={path} />],
    ["/account/house-rules/", () => <HouseRulesPage api={api} path={path} />],
    ["/account/homebrew/", () => <HomebrewPageView api={api} path={path} />],
    ["/account/sharing/", () => <SharingPage api={api} campaignRef={refFromSharingPath(path)} />],
  ];
  const hit = table.find(([prefix]) => path.startsWith(prefix) || path === prefix.slice(0, -1));
  return hit ? hit[1]() : null;
};

const everyPage = (): SectionPage[] => {
  const parts = sectionPages(REF, { isDm: true, importsOn: true });
  return [...parts.story, ...parts.add, ...parts.rules];
};

describe("every page a DM reaches from a campaign's tabs keeps its navigation", () => {
  for (const page of everyPage()) {
    it(`${page.key} (${page.href.split("?")[0]}) marks itself in the campaign's navigation`, async () => {
      const api = mockApi("table");
      const element = pageAt(api, page.href);
      // A page with no component here fails the test: a new page must be walked too.
      if (element === null) throw new Error(`no walk for ${page.key} at ${page.href}`);
      render(element);
      await waitFor(() => {
        const pages = screen.getByRole("navigation", { name: words.pagesLabel });
        expect(pages.querySelector('[aria-current="page"]')?.textContent).toBe(page.label);
      });
    });
  }

  it("the Shared rules page for a DM points at the campaign's own page", () => {
    const shared = everyPage().find((p) => p.key === "sharedRules");
    expect(shared?.href).toBe(`/account/sharing/${encodeURIComponent(REF)}`);
  });
});
