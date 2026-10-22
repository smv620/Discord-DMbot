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
import { type AccountApi, ApiError, type CampaignPage } from "./api";

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

  async function load() {
    if (!ref || !api.campaignPage) return setView("none");
    setView("loading");
    try {
      setPage(await api.campaignPage(ref));
      setView("ok");
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
  const dm: [string, string, string][] = [];
  if (RECAPS_LIVE) dm.push([`/account/recaps?campaign=${e}`, words.recaps, words.recapsNote]);
  dm.push([`/account/names/${e}`, words.names, words.namesNote]);
  if (IMPORT_LIVE || page.importsOn) dm.push([`/account/import/${e}`, words.importSessions, words.importNote]);
  // House rules and homebrew are read by anyone in the server (only changing them is the DM's, in
  // Discord), so they are for the whole table.
  const table: [string, string, string][] = [
    [`/account/house-rules/${e}`, words.houseRules, words.houseRulesNote],
    [`/account/homebrew/${e}`, words.homebrew, words.homebrewNote],
    [`/account/transcripts/${e}`, words.transcripts, words.transcriptsNote],
    [`/account/web/${e}`, words.story, words.storyNote],
  ];
  return (
    <div class="stack campaign-page">
      {back}
      <p class="muted">{words.lead(page.serverName)}</p>
      {isDm && <LinkList heading={words.dmHeading} id="campaign-dm" links={dm} />}
      <LinkList heading={words.tableHeading} id="campaign-table" links={table} />
    </div>
  );
}

function LinkList({ heading, id, links }: { heading: string; id: string; links: [string, string, string][] }) {
  return (
    <section class="stack" aria-labelledby={id}>
      <h2 id={id}>{heading}</h2>
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
