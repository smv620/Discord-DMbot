/** @jsxImportSource preact */
// "Words DMbot stopped asking about" on a campaign's Settings page (#3577, the website half of #3033), for its game masters:
// the words a DM said Never or Not now to when DMbot offered to make them a kind of connection, with a button to lift the
// answer so the words may be offered again after the next session. The bot applies the press; the list is read again after.
// One press at a time. The words are the table's own, in quotes, shown to the DM only. Hidden while it can't be read (a
// player never reaches this page's data) and while KIND_OFFERS_LIVE is off (the Settings page decides).
import { useEffect, useRef, useState } from "preact/hooks";

import { kindOffersWords as w } from "../content/kind-offers-words";
import { ApiError, type AccountApi, type KindOffer } from "./api";
import SavingWait from "./SavingWait";

export default function KindOffersPanel({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [offers, setOffers] = useState<KindOffer[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const flight = useRef(false);
  const status = useRef<HTMLParagraphElement>(null);

  async function read(): Promise<void> {
    if (!api.campaignKindOffers) return;
    try {
      setOffers(await api.campaignKindOffers(campaignRef));
      setFailed(false);
    } catch (error) {
      // Not a game master of it, or not there: nothing to show. Anything else is said.
      if (error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found" || error.kind === "signed-out")) setOffers(null);
      else setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function lift(offer: KindOffer): Promise<void> {
    if (flight.current || !api.liftKindOffer) return;
    flight.current = true;
    setBusy(true);
    setProblem(null);
    setSaid("");
    try {
      await api.liftKindOffer(campaignRef, offer.words);
      setSaid(w.lifted(offer.words));
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "server";
      setProblem(Object.hasOwn(w.problems, kind) ? (w.problems[kind] as string) : (w.problems["server"] as string));
    } finally {
      flight.current = false;
      setBusy(false);
    }
    await read(); // (the change is made: a failed read must not read as a failed change)
    status.current?.focus(); // the pressed row is gone: the keyboard goes to what was said
  }

  if (failed) return <p class="warn small" role="alert">{w.heading}: {w.loadFailed}</p>;
  if (offers === null) return null;
  return (
    <section class="panel stack" aria-labelledby="kind-offers-h">
      <h2 id="kind-offers-h">{w.heading}</h2>
      <p class="small muted">{w.lead}</p>
      {offers.length === 0 ? (
        <p class="muted">{w.empty}</p>
      ) : (
        <ul class="stack plain-list">
          {offers.map((offer) => (
            <li key={offer.words} class="stack">
              <p>{w.quoted(offer.words)}</p>
              <p class="small muted">{offer.state === "never" ? w.never : w.later}</p>
              <p>
                <button type="button" class="button secondary" aria-disabled={busy} aria-label={w.liftFor(offer.words)} onClick={() => !busy && void lift(offer)}>
                  {w.lift}
                </button>
              </p>
            </li>
          ))}
        </ul>
      )}
      {/* Always there, so a screen reader hears what is put in it. */}
      <SavingWait active={busy} />
      <p class="small" role="status" tabIndex={-1} ref={status}>{said}</p>
      {problem && <p class="warn small" role="alert">{problem}</p>}
    </section>
  );
}
