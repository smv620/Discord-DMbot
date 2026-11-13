/** @jsxImportSource preact */
// The lineage and class question on the Questions page (#3946, the web half of #3926; core web/trait_offers.py). A lore upload
// said something about a character the campaign already has ("Torvin is a dwarf") that would change them, so DMbot asks first.
// One question at a time, in Discord's words: the question, where the lore said it, why DMbot asks, then Yes or No. A yes is
// applied by the bot as the DM's own change; a no is remembered so the same trait is never asked again. DM only: the door sends
// the quote and the upload's name to no one else. The wait shows its stage, how long and, after a minute, that it is slow.
import { useEffect, useRef, useState } from "preact/hooks";

import { questionOf, traitWords as w } from "../content/trait-questions-words";
import { ApiError, type AccountApi, type TraitOffer } from "./api";
import SavingWait from "./SavingWait";

/** The plain line for how an answer ended: the bot's own word (`gone`, `refused`, `not-allowed`, `failed`) or the door's `lost`. */
function sayFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "still-waiting") return w.stillWorking;
  if (error.kind === "gone" || error.note === "gone") return w.gone;
  if (error.note === "refused") return w.refused;
  if (error.kind === "not-allowed" || error.note === "not-allowed") return w.notAllowed;
  return w.couldNot; // (failed, lost, anything else: try again in a minute)
}

const QUOTE_SHOWN = 200; // as Discord's card: the quote stays short

export default function TraitQuestions({ api, campaignRef, id }: { api: AccountApi; campaignRef: string; id: string }) {
  const [list, setList] = useState<TraitOffer[] | null>(null);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const flight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.traitOffers) return;
    try {
      const got = await api.traitOffers(campaignRef);
      if (live.current) (setList(got), setFailed(false));
    } catch {
      if (live.current) setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function answer(offer: TraitOffer, how: "keep" | "never"): Promise<void> {
    if (!api.answerTrait || flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await api.answerTrait(campaignRef, offer, how);
      if (live.current) setSaid({ ok: true, text: how === "keep" ? w.kept(offer.who, offer.name.toLowerCase(), offer.kind === "species") : w.left(offer.who, offer.name.toLowerCase()) });
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  if (failed && !list) return <p class="warn" role="alert">{w.failed}</p>;
  if (!list) return <p class="muted" role="status">{w.loading}</p>;
  const offer = list[0];
  const q = offer ? questionOf(offer) : null;
  const quote = offer ? offer.quote.split(/\s+/).filter(Boolean).join(" ") : "";
  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>
        {w.heading}
        {list.length > 1 ? ` (${w.waiting(list.length)})` : ""}
      </h3>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? said.text : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? said.text : ""}
      </p>
      {offer && q ? (
        <>
          <p>
            <strong>{q.ask}</strong> {q.has}
          </p>
          <p class="small">
            {w.source(offer.upload)}
            {quote ? ":" : "."}
          </p>
          {quote && <blockquote class="small">“{quote.length > QUOTE_SHOWN ? `${quote.slice(0, QUOTE_SHOWN - 1).trimEnd()}…` : quote}”</blockquote>}
          <p class="small">{w.why}</p>
          <p class="small">
            {w.yes}: {q.yes} {w.no}: {q.no}, and DMbot won't ask this again.
          </p>
          <div class="row">
            <button type="button" class="button" disabled={busy} onClick={() => void answer(offer, "keep")}>
              {w.yes}
            </button>
            <button type="button" class="button secondary" disabled={busy} onClick={() => void answer(offer, "never")}>
              {w.no}
            </button>
          </div>
          <SavingWait active={busy} stage={w.saving} />
        </>
      ) : (
        <p class="muted">{w.nothingLeft}</p>
      )}
    </section>
  );
}
