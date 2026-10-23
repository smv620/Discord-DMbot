/** @jsxImportSource preact */
// One campaign's own page (#2416): the campaign's name, a way back to My Campaigns, and a link to
// each page the website has for it, already opened on this campaign. Who sees which link is the
// API's role for the person: the DM's tools (recaps, house rules, homebrew, names, importing) only
// to the campaign's DMs, and for the whole table the transcripts and the story map. The page only
// shows what the role says; every page behind a link checks again.
import { useEffect, useState } from "preact/hooks";

import { campaignPage as words } from "../content/campaign-page";
import { IMPORT_LIVE } from "../content/import-words";
import { RECAPS_LIVE } from "../content/recap-words";
import { type AccountApi, ApiError, type Campaign, type CampaignPage } from "./api";
import CampaignFacts from "./CampaignFacts";
import CampaignNav from "./CampaignNav";

/** The campaign in the address: `/account/campaigns/<server>.<campaign>`. */
export function campaignRefFromPath(path: string): string | null {
  const match = /^\/account\/campaigns\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null; // a broken %-escape is just a campaign that isn't there
  }
}

type View = "loading" | "ok" | "none" | "down" | "signed-out";

export default function CampaignPageView({ api, path }: { api: AccountApi; path: string }) {
  const ref = campaignRefFromPath(path);
  const [view, setView] = useState<View>("loading");
  const [page, setPage] = useState<CampaignPage | null>(null);
  // The campaign's card on My Account: its facts for this page's heading (left out when the API
  // cannot say, or the person is not in /me's list).
  const [card, setCard] = useState<Campaign | null>(null);

  async function load() {
    if (!ref || !api.campaignPage) return setView("none");
    setView("loading");
    try {
      setPage(await api.campaignPage(ref));
      setView("ok");
      void Promise.resolve(api.me())
        .then((me) => setCard(me?.campaigns.find((c) => c.ref === ref) ?? null))
        .catch(() => undefined);
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      setView(kind === "signed-out" ? "signed-out" : kind === "not-found" || kind === "not-allowed" ? "none" : "down");
    }
  }
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, path]);

  // The campaign's name is the page's heading and title, so a screen reader and the browser's tab say
  // which campaign this is (the page's own heading reads "Campaign" until it is known).
  useEffect(() => {
    if (!page) return;
    const h1 = document.querySelector(".account h1");
    if (h1) h1.textContent = page.name;
    document.title = `${page.name} · DMbot`;
  }, [page]);

  const back = (
    <p>
      <a href="/account#campaigns">{words.backToCampaigns}</a>
    </p>
  );
  if (view === "signed-out")
    return (
      <div class="panel" role="status">
        <p>{words.signedOut}</p>
      </div>
    );
  if (view === "none" || !ref)
    return (
      <div class="stack">
        <p role="status">{words.notFound}</p>
        {back}
      </div>
    );
  if (view === "down")
    return (
      <div class="stack">
        <p class="note bad" role="alert">
          {words.down}
        </p>
        <button type="button" class="button secondary" onClick={() => void load()}>
          {words.tryAgain}
        </button>
        {back}
      </div>
    );
  if (view === "loading" || !page)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );

  // Fail closed: only a role the API names as a DM role shows the DM tools (a missing or new one does not).
  const isDm = page.role === "owner" || page.role === "co-dm";
  const e = encodeURIComponent(ref);
  type Link = [href: string, label: string, note: string];
  const story: Link[] = [];
  if (isDm && RECAPS_LIVE) story.push([`/account/recaps?campaign=${e}`, words.pages.recaps, words.pageNotes.recaps]);
  story.push([`/account/web/${e}`, words.pages.graph, words.pageNotes.graph]);
  if (isDm) story.push([`/account/names/${e}`, words.pages.names, words.pageNotes.names]);
  const table: Link[] = [[`/account/transcripts/${e}`, words.pages.transcripts, words.pageNotes.transcripts]];
  if (isDm && (IMPORT_LIVE || page.importsOn)) table.push([`/account/import/${e}`, words.pages.import, words.pageNotes.import]);
  // House rules and homebrew are read by anyone in the server (only changing them is the DM's, in
  // Discord), so they are for the whole table.
  const rules: Link[] = [
    [`/account/house-rules/${e}`, words.pages.houseRules, words.pageNotes.houseRules],
    [`/account/homebrew/${e}`, words.pages.homebrew, words.pageNotes.homebrew],
  ];
  if (isDm) rules.push(["/account/sharing", words.sharedRules, words.sharedRulesNote]);
  const mine = card ?? null;
  return (
    <div class="stack campaign-page">
      <CampaignNav api={api} campaignRef={ref} page="campaign" name={page.name} />
      <p class="muted">{words.lead(page.serverName)}</p>
      {mine && <CampaignFacts campaign={mine} />}
      <LinkList heading={words.groups.story} id="story" links={story} />
      <LinkList heading={words.groups.table} id="table" links={table} />
      <LinkList heading={words.groups.rules} id="rules" links={rules} />
      {isDm && <SettingsGroup page={page} />}
    </div>
  );
}

function LinkList({ heading, id, links }: { heading: string; id: string; links: [string, string, string][] }) {
  return (
    <section class="stack" aria-labelledby={`${id}-heading`} id={id}>
      <h2 id={`${id}-heading`}>{heading}</h2>
      <ul class="campaigns">
        {links.map(([href, label, note]) => (
          <li key={href}>
            <a href={href}>{label}</a>
            <span class="small muted block">{note}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The campaign's current choices, for its game masters. Read-only: where the website cannot change
 * one yet, the line says where to in Discord (the same names as Discord's ⚙️ Settings). */
function SettingsGroup({ page }: { page: CampaignPage }) {
  const s = page.settings;
  const rows: [key: keyof typeof words.paths, value: string | null][] = [
    ["visibility", s ? ((words.visibility as Record<string, string>)[s.visibility] ?? null) : null],
    ["level", s ? ((words.level as Record<string, string>)[s.level] ?? null) : null],
    ["retention", s ? (s.keepDays === null ? words.retentionForever : words.retentionDays(s.keepDays)) : null],
    ["gameMasters", s ? words.gameMastersCount(s.gameMasters) : null],
    ["handOver", null],
    ["pause", s ? (s.paused ? words.pausedYes : words.pausedNo) : null],
    ["delete", null],
  ];
  return (
    <section class="stack" aria-labelledby="settings-heading" id="settings">
      <h2 id="settings-heading">{words.groups.settings}</h2>
      <p class="muted small">{words.settingsLead}</p>
      <ul class="campaigns">
        {rows.map(([key, value]) => (
          <li key={key}>
            <span class="campaign-name">{words.settingsLabels[key]}</span>
            {value !== null && <span class="block">{value}</span>}
            <span class="small muted block">{words.doThisInDiscord(words.paths[key])}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
