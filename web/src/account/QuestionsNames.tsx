/** @jsxImportSource preact */
// The new names to check of one campaign, on the Questions page (#3438): one names-to-check list for each recap
// that has names waiting, oldest session first. Each list is the recap page's own (`RecapNameChecks`), so the card,
// its "Where was it said?", the split and group buttons, Undo and the wait panel are the same and answer the same
// way; this only finds which recaps to open. Each opens its code only when it is shown (the Questions page stays
// light). Ids, session dates and counts only come from the campaign's read.
import { useEffect, useState } from "preact/hooks";

import { questionsPage as words } from "../content/questions-words";
import { formatDate } from "../content/whats-new-date";
import { type AccountApi, type CampaignNameChecks } from "./api";
import LazyPanel from "./LazyPanel";

const day = (at: number): string => formatDate(new Date(at * 1000).toISOString().slice(0, 10));

export default function QuestionsNames({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<CampaignNameChecks | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!api.campaignNameChecks) return;
    let live = true;
    api
      .campaignNameChecks(campaignRef)
      .then((got) => live && setList(got))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [api, campaignRef]);

  if (failed)
    return (
      <p class="warn" role="alert">
        {words.failed}
      </p>
    );
  if (list === null)
    return (
      <p class="muted" role="status">
        {words.loading}
      </p>
    );
  if (list.recaps.length === 0) return <p class="small muted">{words.noneHere}</p>;
  return (
    <div class="stack">
      {list.recaps.map((r) => (
        <section class="stack" key={r.ref} aria-label={words.sessionOf(day(r.at))}>
          <h3>{words.sessionOf(day(r.at))}</h3>
          <LazyPanel load={() => import("./RecapNameChecks")} what="the names to check" props={{ api, refId: r.ref }} />
        </section>
      ))}
    </div>
  );
}
