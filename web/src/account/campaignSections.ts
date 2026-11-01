// The parts of a campaign's website and the pages in each (#2985; docs/PLAN.md, "Where everything is on the
// website"). One list, used by the tabs on every campaign page and by the campaign's own Overview page, so the
// two never disagree. The parts are named for what a DM is doing: read the story, add to it, set the rules.
import { campaignPage as words } from "../content/campaign-page";
import { IMPORT_LIVE } from "../content/import-words";
import { LORE_LIVE } from "../content/lore-words";
import { RECAP_STYLE_LIVE } from "../content/recap-style-words";
import { RECAPS_LIVE } from "../content/recap-words";

export type CampaignGroup = "overview" | "story" | "add" | "rules" | "settings";

/** Which page this is: it decides the last crumb and the marked tab. */
export type CampaignPageKey =
  | "campaign"
  | "recaps"
  | "timeline"
  | "graph"
  | "names"
  | "transcripts"
  | "lore"
  | "recapStyle"
  | "import"
  | "houseRules"
  | "homebrew";

export const GROUP_OF: Record<CampaignPageKey, CampaignGroup> = {
  campaign: "overview",
  recaps: "story",
  timeline: "story",
  graph: "story",
  names: "story",
  transcripts: "story",
  lore: "add",
  recapStyle: "story",
  import: "add",
  houseRules: "rules",
  homebrew: "rules",
};

export interface SectionPage {
  key: CampaignPageKey | "sharedRules";
  href: string;
  label: string;
  note: string;
}

/** The pages in each part for this person. Who sees which is the API's role for them (a DM's tools only to the
 * campaign's DMs); every page behind a link checks again. */
export function sectionPages(ref: string, o: { isDm: boolean; importsOn?: boolean | undefined }): Record<"story" | "add" | "rules", SectionPage[]> {
  const e = encodeURIComponent(ref);
  const story: SectionPage[] = [];
  if (o.isDm && RECAPS_LIVE) story.push({ key: "recaps", href: `/account/recaps?campaign=${e}`, label: words.pages.recaps, note: words.pageNotes.recaps });
  if (o.isDm && RECAP_STYLE_LIVE) story.push({ key: "recapStyle", href: `/account/recap-style/${e}`, label: words.pages.recapStyle, note: words.pageNotes.recapStyle });
  story.push({ key: "timeline", href: `/account/timeline/${e}`, label: words.pages.timeline, note: words.pageNotes.timeline });
  story.push({ key: "graph", href: `/account/web/${e}`, label: words.pages.graph, note: words.pageNotes.graph });
  if (o.isDm) story.push({ key: "names", href: `/account/names/${e}`, label: words.pages.names, note: words.pageNotes.names });
  story.push({ key: "transcripts", href: `/account/transcripts/${e}`, label: words.pages.transcripts, note: words.pageNotes.transcripts });
  const add: SectionPage[] = [];
  if (o.isDm && LORE_LIVE) add.push({ key: "lore", href: `/account/lore/${e}`, label: words.pages.lore, note: words.pageNotes.lore });
  if (o.isDm && (IMPORT_LIVE || o.importsOn)) add.push({ key: "import", href: `/account/import/${e}`, label: words.pages.import, note: words.pageNotes.import });
  // House rules and homebrew are read by anyone in the server (only changing them is the DM's), so they are for the
  // whole table.
  const rules: SectionPage[] = [
    { key: "houseRules", href: `/account/house-rules/${e}`, label: words.pages.houseRules, note: words.pageNotes.houseRules },
    { key: "homebrew", href: `/account/homebrew/${e}`, label: words.pages.homebrew, note: words.pageNotes.homebrew },
  ];
  if (o.isDm) rules.push({ key: "sharedRules", href: "/account/sharing", label: words.sharedRules, note: words.sharedRulesNote });
  return { story, add, rules };
}
