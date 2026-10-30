/** @jsxImportSource preact */
// One campaign's own page (#2416): the campaign's name, a way back to My Campaigns, and a link to
// each page the website has for it, already opened on this campaign. Who sees which link is the
// API's role for the person: the DM's tools (recaps, house rules, homebrew, names, importing) only
// to the campaign's DMs, and for the whole table the transcripts and the story map. The page only
// shows what the role says; every page behind a link checks again.
import { useEffect, useState } from "preact/hooks";

import { campaignPage as words } from "../content/campaign-page";
import { SHEETS_LIVE } from "../content/sheets-switch";
import { type AccountApi, ApiError, type Campaign, type CampaignPage } from "./api";
import CampaignFacts from "./CampaignFacts";
import CampaignNav from "./CampaignNav";
import { type SectionPage, sectionPages } from "./campaignSections";
import CampaignSettings from "./CampaignSettings";
import CampaignDelete from "./CampaignDelete";
import { CAMPAIGN_DELETE_LIVE } from "../content/campaign-delete";
import { rememberDeleted } from "./deleted-notice";
import LazyPanel from "./LazyPanel";

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
  // Try It has no backup, so Delete offers none; the plan comes from the same /me answer as the card.
  const [tryIt, setTryIt] = useState(false);

  async function load() {
    if (!ref || !api.campaignPage) return setView("none");
    setView("loading");
    try {
      setPage(await api.campaignPage(ref));
      setView("ok");
      void Promise.resolve(api.me())
        .then((me) => {
          setCard(me?.campaigns.find((c) => c.ref === ref) ?? null);
          setTryIt(me?.plan?.id === "try-it");
        })
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
        {/* Discord's 🌐 Manage campaign opens this address; signing in brings the person back to it (#2301). */}
        <a class="button" href={api.signInUrl()}>
          {words.signIn}
        </a>
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
  const parts = sectionPages(ref ?? "", { isDm, importsOn: page.importsOn });
  const mine = card ?? null;
  return (
    <div class="stack campaign-page">
      <CampaignNav api={api} campaignRef={ref} page="campaign" name={page.name} />
      <p class="muted">{words.lead(page.serverName)}</p>
      {mine && <CampaignFacts campaign={mine} />}
      <LinkList heading={words.groups.story} id="story" links={parts.story} />
      {parts.add.length > 0 && <LinkList heading={words.groups.add} id="add" links={parts.add} />}
      <LinkList heading={words.groups.rules} id="rules" links={parts.rules} />
      {SHEETS_LIVE && api.campaignSheets && (
        <LazyPanel load={() => import("./Sheets")} what="the character sheets" props={{ api, campaignRef: ref }} />
      )}
      {isDm && <CampaignSettings key={ref} api={api} campaignRef={ref} page={page} />}
      {/* Delete is the last thing on the page (#2879): the person who holds the plan, and only while the switch is on. */}
      {/* Only a DM is offered it; the plan holder (or the owner, without the flag) within the DMs. */}
      {isDm && CAMPAIGN_DELETE_LIVE && ref && api.deleteCampaign && (card?.canDelete ?? page.role === "owner") && (
        <div class="stack">
          <CampaignDelete
            api={api}
            refId={ref}
            name={page.name}
            tryIt={tryIt}
            onDeleted={(channelsLeft) => {
              rememberDeleted(page.name, channelsLeft);
              window.location.assign("/account#campaigns");
            }}
            onGone={() => window.location.assign("/account#campaigns")}
          />
          <p class="small muted">{words.deleteInDiscord(words.paths.delete)}</p>
        </div>
      )}
    </div>
  );
}

function LinkList({ heading, id, links }: { heading: string; id: string; links: SectionPage[] }) {
  return (
    <section class="stack" aria-labelledby={`${id}-heading`} id={id}>
      <h2 id={`${id}-heading`}>{heading}</h2>
      <ul class="campaigns">
        {links.map(({ href, label, note }) => (
          <li key={href}>
            <a href={href}>{label}</a>
            <span class="small muted block">{note}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
