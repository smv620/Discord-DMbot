/** @jsxImportSource preact */
// What every page of one campaign starts with (#2461; docs/PLAN.md, "Where everything is on the
// website"): the breadcrumb "My Account › <Campaign> › <Page>", the campaign's tabs (Overview, Story,
// Add to the story, Rules, Settings) with the one this page belongs to marked, and under them the pages in
// that part, so a DM always sees where they are and what else is next to it (#2985). It replaces the old
// "← <campaign>" back links, so nobody is lost. The Settings tab is only for the campaign's game
// masters, as the API names them (a missing or new role shows no Settings: it fails closed).
import type { ComponentType } from "preact";
import { useEffect, useState } from "preact/hooks";

import { campaignPage as words } from "../content/campaign-page";
import { type AccountApi, type CampaignPage } from "./api";
import { campaignHref } from "./campaignLinks";
import { QUESTIONS_LIVE } from "../content/questions-switch";
import { type CampaignGroup, type CampaignPageKey, GROUP_OF, sectionPages } from "./campaignSections";

export type { CampaignGroup, CampaignPageKey };

// One read of the campaign's page per campaign, kept for this page load and shared by the navs that
// ask, so a page with two of them does not ask twice. (The campaign's own page reads for itself,
// because it must tell "not found" from "down"; this one only wants the role and the name.)
const reads = new WeakMap<AccountApi, Map<string, Promise<CampaignPage | null>>>();

export function readCampaign(api: AccountApi, ref: string): Promise<CampaignPage | null> {
  if (!api.campaignPage) return Promise.resolve(null);
  let byRef = reads.get(api);
  if (!byRef) reads.set(api, (byRef = new Map()));
  let found = byRef.get(ref);
  if (!found) {
    // Opening a campaign's pages is a visit, noted once for each page load (#5037): the account page's order
    // follows it. A failure is no reason to hold the page.
    void api.visitCampaign?.(ref)?.catch(() => undefined);
    // A failed read is not kept, so the next page (or a retry) asks again instead of showing no
    // Settings tab for good.
    found = api.campaignPage(ref).catch(() => {
      byRef.delete(ref);
      return null;
    });
    byRef.set(ref, found);
  }
  return found;
}

/** The campaign's name as the first heading of the page, and the page's own title under it (#5038; the owner,
 * 2026-11-22: "the actively selected campaign title should always be on top of every page"). The page's static
 * heading (the page's title, drawn before any script runs) becomes the second-level heading and the campaign's
 * name is put above it, at the top of the page's content, so on a phone the name is not a screen down. Done once for
 * each page (a page with two navs has one name), the space is kept while the name is loading (and the empty heading
 * is hidden from a screen reader until it has words), and it is undone when the last nav leaves, so a page of the
 * island that is not a campaign's keeps its own title. The rule it relies on: the page's static `<h1>` is a direct
 * child of the page's one `.account` (every `.astro` page of the account is). The campaign's own page already makes
 * its one heading the name (CampaignPage), so it is left alone. */
let navsOnPage = 0;

function useCampaignHeading(name: string | null, skip: boolean): void {
  useEffect(() => {
    if (skip || typeof document === "undefined") return;
    const root = document.querySelector<HTMLElement>(".account");
    if (!root) return;
    navsOnPage += 1;
    let title = root.querySelector<HTMLElement>(":scope > [data-campaign-title]");
    if (!title) {
      title = document.createElement("h1");
      title.setAttribute("data-campaign-title", "");
      title.className = "campaign-title";
      title.setAttribute("aria-hidden", "true"); // (until it has words)
      const pageTitle = root.querySelector<HTMLElement>(":scope > h1");
      if (pageTitle) {
        // The page's own title goes one level down, in the same place, with whatever it had.
        const sub = document.createElement("h2");
        for (const attr of Array.from(pageTitle.attributes)) sub.setAttribute(attr.name, attr.value);
        sub.setAttribute("data-page-title", "");
        sub.append(...Array.from(pageTitle.childNodes));
        pageTitle.replaceWith(sub);
      }
      root.insertBefore(title, root.firstChild);
    }
    return () => {
      navsOnPage -= 1;
      if (navsOnPage > 0) return;
      // The last nav went (another page of the island, or another campaign): the page is as it was served.
      root.querySelector(":scope > [data-campaign-title]")?.remove();
      const sub = root.querySelector<HTMLElement>(":scope > [data-page-title]");
      if (sub) {
        const back = document.createElement("h1");
        for (const attr of Array.from(sub.attributes)) if (attr.name !== "data-page-title") back.setAttribute(attr.name, attr.value);
        back.append(...Array.from(sub.childNodes));
        sub.replaceWith(back);
      }
    };
  }, [skip]);
  useEffect(() => {
    if (skip || typeof document === "undefined") return;
    const title = document.querySelector<HTMLElement>(".account > [data-campaign-title]");
    if (!title) return;
    title.textContent = name ?? "";
    if (name) title.removeAttribute("aria-hidden");
    else title.setAttribute("aria-hidden", "true");
  }, [name, skip]);
}

export default function CampaignNav({
  api,
  campaignRef,
  page,
  name,
  label,
}: {
  api: AccountApi;
  campaignRef: string;
  /** Which page this is. `label` overrides the last crumb, such as one NPC's or one recap's name. */
  page: CampaignPageKey;
  name?: string | null;
  label?: string;
}) {
  const [found, setFound] = useState<CampaignPage | null>(null);
  useEffect(() => {
    let live = true;
    void readCampaign(api, campaignRef).then((got) => live && setFound(got));
    return () => {
      live = false;
    };
  }, [api, campaignRef]);

  // The link to the Questions page is fetched only while its switch is on and only for a game master, so the page
  // does not carry it (the weight budget): the switch is tested here so the build drops the import while it is off.
  const [Questions, setQuestions] = useState<ComponentType<{ api: AccountApi; show: boolean }> | null>(null);
  const isDmNow = found?.role === "owner" || found?.role === "co-dm";
  useEffect(() => {
    if (!QUESTIONS_LIVE || !isDmNow) return;
    void import("./QuestionsLink").then((m) => setQuestions(() => m.default));
  }, [isDmNow]);

  const campaign = name || found?.name || words.thisCampaign;
  useCampaignHeading(name || found?.name || null, page === "campaign");
  // Settings is a part of the campaign's own page: its tab is the marked one once the person has gone there (#settings).
  const [hash, setHash] = useState(typeof window === "undefined" ? "" : window.location.hash);
  useEffect(() => {
    const on = (): void => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const here = page === "campaign" && hash === "#settings" ? "settings" : GROUP_OF[page];
  const isDm = found?.role === "owner" || found?.role === "co-dm";
  const home = campaignHref(campaignRef);
  const listName = page === "campaign" ? null : words.pages[page];
  // A page inside a list (one NPC, one recap) names itself last and links the list before it.
  const listHref = page === "names" ? `/account/names/${encodeURIComponent(campaignRef)}` : `/account/recaps?campaign=${encodeURIComponent(campaignRef)}`;
  const pageName = label ?? listName;
  const parts = sectionPages(campaignRef, { isDm, importsOn: found?.importsOn });
  // Each tab opens the first page of its part (Settings, the Overview's settings); a part with no page for this
  // person (Add to the story, for a player) has no tab.
  const tabs: { group: CampaignGroup; href: string }[] = [{ group: "overview", href: home }];
  for (const group of ["story", "add", "rules"] as const) {
    const first = parts[group][0];
    if (first) tabs.push({ group, href: first.href });
  }
  if (isDm) tabs.push({ group: "settings", href: `${home}#settings` });
  const inPart = here === "story" || here === "add" || here === "rules" ? parts[here] : [];

  return (
    <div class="campaign-nav">
      <nav aria-label={words.crumbsLabel}>
        <ol class="crumbs">
          <li>
            <a href="/account">{words.myAccount}</a>
          </li>
          <li>
            {pageName === null ? <span aria-current="page">{campaign}</span> : <a href={home}>{campaign}</a>}
          </li>
          {label !== undefined && listName !== null && (
            <li>
              <a href={listHref}>{listName}</a>
            </li>
          )}
          {pageName !== null && (
            <li>
              <span aria-current="page">{pageName}</span>
            </li>
          )}
        </ol>
      </nav>
      <nav aria-label={words.tabsLabel}>
        <ul class="tabs">
          {tabs.map(({ group, href }) => (
            <li key={group}>
              <a href={href} aria-current={here === group ? "location" : undefined}>
                {words.groups[group]}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      {/* (the switch is tested here so the build drops the link and its words while it is off: the page stays within its weight budget) */}
      {Questions && <Questions api={api} show={isDm} />}
      {inPart.length > 1 && (
        <nav aria-label={words.pagesLabel}>
          <ul class="tabs subtabs">
            {inPart.map((p) => (
              <li key={p.key}>
                <a href={p.href} aria-current={p.key === page ? "page" : undefined}>
                  {p.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </div>
  );
}
