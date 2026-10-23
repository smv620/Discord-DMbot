/** @jsxImportSource preact */
// What every page of one campaign starts with (#2461; docs/PLAN.md, "Where everything is on the
// website"): the breadcrumb "My Account › <Campaign> › <Page>" and the campaign's four tabs (Story,
// At the table, Rules, Settings), with the one this page belongs to marked. It replaces the old
// "← <campaign>" back links, so nobody is lost. The Settings tab is only for the campaign's game
// masters, as the API names them (a missing or new role shows no Settings: it fails closed).
import { useEffect, useState } from "preact/hooks";

import { campaignPage as words } from "../content/campaign-page";
import { type AccountApi, type CampaignPage } from "./api";
import { campaignHref } from "./campaignLinks";

export type CampaignGroup = "story" | "table" | "rules" | "settings";

/** Which page this is: it decides the last crumb and the marked tab. */
export type CampaignPageKey =
  | "campaign"
  | "recaps"
  | "timeline"
  | "graph"
  | "names"
  | "transcripts"
  | "import"
  | "houseRules"
  | "homebrew";

const GROUP_OF: Record<CampaignPageKey, CampaignGroup | null> = {
  campaign: null,
  recaps: "story",
  timeline: "story",
  graph: "story",
  names: "story",
  transcripts: "table",
  import: "table",
  houseRules: "rules",
  homebrew: "rules",
};

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
  const pageName = label ?? (page === "campaign" ? null : words.pages[page]);
  const tabs: CampaignGroup[] = isDm ? ["story", "table", "rules", "settings"] : ["story", "table", "rules"];

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
          {pageName !== null && (
            <li>
              <span aria-current="page">{pageName}</span>
            </li>
          )}
        </ol>
      </nav>
      <nav aria-label={words.tabsLabel}>
        <ul class="tabs">
          {tabs.map((group) => (
            <li key={group}>
              <a href={`${home}#${group}`} aria-current={here === group ? "page" : undefined}>
                {words.groups[group]}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
