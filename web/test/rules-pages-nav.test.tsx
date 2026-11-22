/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Every Rules page a DM reaches from a campaign's tabs keeps the campaign's navigation (#5039): House rules, Homebrew and
// Shared rules. This walks the pages `sectionPages` gives a DM for the Rules tab and renders each one. The other parts' pages
// are not walked here yet; see the PR.
import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import HouseRulesPage from "../src/account/HouseRules";
import HomebrewPageView from "../src/account/Homebrew";
import SharingPage, { refFromSharingPath } from "../src/account/Sharing";
import { sectionPages } from "../src/account/campaignSections";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";

const pageFor: Record<string, (api: ReturnType<typeof mockApi>, path: string) => preact.JSX.Element> = {
  houseRules: (api, path) => <HouseRulesPage api={api} path={path} />,
  homebrew: (api, path) => <HomebrewPageView api={api} path={path} />,
  sharedRules: (api, path) => <SharingPage api={api} campaignRef={refFromSharingPath(path)} />,
};

describe("a campaign's Rules pages keep its navigation", () => {
  const rules = sectionPages(REF, { isDm: true }).rules;
  for (const page of rules) {
    const key = page.key;
    // A new DM Rules page must be walked here too: an unlisted key fails, rather than being skipped.
    if (!pageFor[key]) throw new Error(`no walk for the Rules page ${key}`);
    it(`${key} (${page.href}) renders the campaign's navigation`, async () => {
      render(pageFor[key]!(mockApi("table"), page.href));
      await waitFor(() => expect(screen.getAllByRole("navigation").length).toBeGreaterThan(0));
    });
  }

  it("Shared rules is in the Rules tab for a DM, and points at the campaign's own page", () => {
    const shared = rules.find((p) => p.key === "sharedRules");
    expect(shared?.href).toBe(`/account/sharing/${encodeURIComponent(REF)}`);
  });
});
