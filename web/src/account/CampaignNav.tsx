/** @jsxImportSource preact */
// What every page of one campaign starts with (#2461; docs/PLAN.md, "Where everything is on the
// website"): the breadcrumb "My Account › <Campaign> › <Page>", the campaign's tabs (Overview, Story,
// Add to the story, Rules, Settings) with the one this page belongs to marked, and under them the pages in
// that part, so a DM always sees where they are and what else is next to it (#2985). It replaces the old
// "← <campaign>" back links, so nobody is lost. The Settings tab is only for the campaign's game
// masters, as the API names them (a missing or new role shows no Settings: it fails closed).
import { useEffect, useState } from "preact/hooks";

import { campaignPage as words } from "../content/campaign-page";
import { type AccountApi, type CampaignPage } from "./api";
import { campaignHref } from "./campaignLinks";
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

  const campaign = name || found?.name || words.thisCampaign;
  const here = GROUP_OF[page];
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
