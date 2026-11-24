/** @jsxImportSource preact */
// The new names to check of one campaign, on the Questions page (#3438): one names-to-check list for each recap
// that has names waiting, oldest session first. Each list is the recap page's own (`RecapNameChecks`), so the card,
// its "Where was it said?", the split and group buttons, Undo and the wait panel are the same and answer the same
// way; this only finds which recaps to open. Each opens its code only when it is shown (the Questions page stays
// light). Ids, session dates and counts only come from the campaign's read. Its `onLeft` is the names still waiting
// in all its recaps, told once every recap shown has reported (a recap that never reads keeps the part open, #4740).
import { useEffect, useState } from "preact/hooks";

import { questionsPage as words } from "../content/questions-words";
import { formatDate } from "../content/whats-new-date";
import { type AccountApi, type CampaignNameChecks } from "./api";
import LazyPanel from "./LazyPanel";

const day = (at: number): string => formatDate(new Date(at * 1000).toISOString().slice(0, 10));

export default function QuestionsNames({
  api,
  campaignRef,
  id,
  onLeft,
  waiting = 0,
}: {
  api: AccountApi;
  campaignRef: string;
  id?: string;
  onLeft?: (left: number) => void;
  /** How many names the campaign's count says are waiting (#5186): more than the recaps hold means some wait outside a recap. */
  waiting?: number;
}) {
  const [list, setList] = useState<CampaignNameChecks | null>(null);
  const [failed, setFailed] = useState(false);
  // Each recap's names still waiting, by the recap's ref, as each recap reports (#4740).
  const [left, setLeft] = useState<Record<string, number>>({});
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

  useEffect(() => {
    if (!list || !onLeft) return;
    if (list.recaps.length === 0) return onLeft(0); // nothing to check: the part is empty at once
    const refs = list.recaps.map((r) => r.ref);
    if (refs.every((r) => r in left)) onLeft(refs.reduce((n, r) => n + (left[r] ?? 0), 0));
  }, [list, left]);

  // One wrapper with the id the toggle points at (aria-controls), whatever the list is saying.
  return (
    <div id={id} class="stack">
      {failed ? (
        <p class="warn" role="alert">
          {words.failed}
        </p>
      ) : list === null ? (
        <p class="muted" role="status">
          {words.loading}
        </p>
      ) : list.recaps.length === 0 ? (
        <p class="small muted">{waiting > 0 ? words.outsideRecap(waiting) : words.noneHere}</p>
      ) : (
        list.recaps.map((r) => (
          <section class="stack" key={r.ref} aria-labelledby={`qn-${r.ref}`}>
            <h3 id={`qn-${r.ref}`}>{words.sessionOf(day(r.at))}</h3>
            <LazyPanel
              load={() => import("./RecapNameChecks")}
              what="the names to check"
              props={{ api, refId: r.ref, onLeft: (n: number) => setLeft((was) => ({ ...was, [r.ref]: n })) }}
            />
          </section>
        ))
      )}
    </div>
  );
}
