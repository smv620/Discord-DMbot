/** @jsxImportSource preact */
// "Is this the same place?" on the Names page (#3533, the website half of #3037): a new spelling of a place the
// campaign already has (Northhaven / North Haven) waits here as a question. Yes makes it another name of the place; No
// keeps two places and is remembered, so the pair is never offered again. Same words and buttons as Discord's question
// (#3532). DM only, and only while PLACE_SPELLING_LIVE; the Names page decides both. One press at a time.
import { useEffect, useRef, useState } from "preact/hooks";

import { placeSpellingWords as w } from "../content/place-spelling-words";
import { ApiError, type AccountApi, type PlaceSpelling } from "./api";

export default function PlaceSpellings({ api, campaignRef, onSignedOut, onChanged }: { api: AccountApi; campaignRef: string; onSignedOut: () => void; onChanged?: () => void }) {
  const [items, setItems] = useState<PlaceSpelling[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [problem, setProblem] = useState(false);
  const flight = useRef(false);
  useEffect(() => {
    if (!api.placeSpellings) return;
    let live = true;
    api
      .placeSpellings(campaignRef)
      .then((got) => live && setItems(got))
      .catch((error) => {
        if (!live) return;
        if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut();
        setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [api, campaignRef, onSignedOut]);

  async function answer(item: PlaceSpelling, yes: boolean): Promise<void> {
    if (flight.current || !api.answerPlaceSpelling) return;
    flight.current = true;
    setBusy(item.id);
    setProblem(false);
    try {
      await api.answerPlaceSpelling(campaignRef, item.id, yes);
      setItems((now) => (now ?? []).filter((x) => x.id !== item.id));
      setSaid(yes ? w.saidYes(item.spelling, item.place) : w.saidNo(item.spelling, item.place));
      onChanged?.();
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut();
      setProblem(true);
    } finally {
      flight.current = false;
      setBusy(null);
    }
  }

  if (failed) return <p class="warn small" role="alert">{w.loadFailed}</p>;
  // Nothing to ask: no heading, no empty box. A answer just given is still said.
  if (!items || (items.length === 0 && !said)) return null;
  return (
    <section class="panel stack" aria-labelledby="place-spelling-h">
      <h2 id="place-spelling-h">{w.heading}</h2>
      <ul class="stack plain-list">
        {items.map((item) => (
          <li key={item.id} class="stack">
            <p>
              <strong>{item.spelling}</strong>
              {w.looksMiddle}
              <strong>{item.place}</strong>
              {w.looksSuffix}
            </p>
            <p class="small muted">{w.lead}</p>
            <div class="row">
              <button type="button" class="button" aria-disabled={busy !== null} aria-label={w.yesFor(item.spelling, item.place)} onClick={() => busy === null && void answer(item, true)}>
                {w.yes}
              </button>
              <button type="button" class="button secondary" aria-disabled={busy !== null} aria-label={w.noFor(item.spelling, item.place)} onClick={() => busy === null && void answer(item, false)}>
                {w.no}
              </button>
            </div>
          </li>
        ))}
      </ul>
      {busy !== null && <p class="small" role="status">{w.working}</p>}
      {said && busy === null && <p class="small" role="status">{said}</p>}
      {problem && <p class="warn small" role="alert">{w.failed}</p>}
    </section>
  );
}
