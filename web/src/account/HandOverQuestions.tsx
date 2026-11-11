/** @jsxImportSource preact */
// "Does Claude still have it?" on the Questions page (#4122, the web half of #4115; core web/hand_over.py): a one-of-a-kind item
// that two characters now hold, asked once, in Discord's words. No, not any more makes the first holder's part history (and can be
// taken back); Yes, both have it keeps both and is never asked again; Not now sends nothing. The bot applies an answer as the DM's
// own change. DM only. The wait shows its stage, how long and, after a minute, that it is slow. Names are only text on the page.
import { useEffect, useRef, useState } from "preact/hooks";

import { cardOf, handOverWords as w } from "../content/hand-over-words";
import { ApiError, type AccountApi, type HandOverQuestion } from "./api";
import SavingWait from "./SavingWait";

/** The plain line for how an answer ended: the bot's own word in `note` (`gone`, `not-allowed`, `too-late`, `changed`, `failed`, `lost`). */
function sayFailure(error: unknown, undoing = false): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "still-waiting") return w.stillWorking;
  if (error.kind === "gone" || error.note === "gone") return w.gone;
  if (error.kind === "not-allowed" || error.note === "not-allowed") return w.notAllowed;
  if (undoing && error.note === "too-late") return w.tooLate; // (only an Undo can be too late or in the way)
  if (undoing && error.note === "changed") return w.changed;
  return w.couldNot; // (failed, lost, anything else: try again in a minute)
}

const key = (q: HandOverQuestion): string => `${q.olderTie}:${q.newerTie}`;

export default function HandOverQuestions({ api, campaignRef, id }: { api: AccountApi; campaignRef: string; id: string }) {
  const [list, setList] = useState<HandOverQuestion[] | null>(null);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [undo, setUndo] = useState<{ q: HandOverQuestion; answerId: string } | null>(null); // the last "no", until something else is answered
  const [skipped, setSkipped] = useState<Set<string>>(new Set()); // "Not now": hidden on this page until it is reloaded
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [notYours, setNotYours] = useState(false); // not a DM of the campaign: the card is simply not there
  const looks = useRef(0); // the newest read wins: reads that overlap cannot land out of order
  const flight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.handOverQuestions) return;
    const mine = (looks.current += 1);
    try {
      const got = await api.handOverQuestions(campaignRef);
      if (live.current && mine === looks.current) (setList(got), setFailed(false));
    } catch (error) {
      if (!live.current || mine !== looks.current) return;
      if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "not-allowed")) setNotYours(true);
      else setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function answer(q: HandOverQuestion, how: "no" | "both"): Promise<void> {
    if (!api.answerHandOver || flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    setUndo(null);
    try {
      const got = await api.answerHandOver(campaignRef, q, how);
      if (live.current) {
        setSaid({ ok: true, text: how === "no" ? w.done(q.olderName, q.newerName, q.itemName) : w.bothKept(q.olderName, q.newerName, q.itemName) });
        if (how === "no" && got.undoable) setUndo({ q, answerId: got.answerId });
      }
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error) });
    } finally {
      await read(); // (what shows is always the API's own)
      // If that read failed, the answered question must not stay up to be pressed again.
      if (live.current) setList((was) => (was ? was.filter((x) => key(x) !== key(q)) : was));
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  async function takeBack(): Promise<void> {
    if (!api.answerHandOver || !undo || flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    const { q, answerId } = undo;
    try {
      await api.answerHandOver(campaignRef, q, "undo", answerId);
      if (live.current) (setSaid({ ok: true, text: w.undone(q.olderName, q.itemName) }), setUndo(null));
    } catch (error) {
      if (live.current) (setSaid({ ok: false, text: sayFailure(error, true) }), setUndo(null));
    } finally {
      await read();
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  if (notYours) return <></>; // (a player or a manager is told nothing: the list is the DM's)
  if (failed && !list) return <p class="warn" role="alert">{w.failed}</p>;
  if (!list) return <p class="muted" role="status">{w.loading}</p>;
  const open = list.filter((q) => !skipped.has(key(q)));
  const q = open[0];
  // Nothing waits and nothing was just said: the panel has nothing to add.
  if (!q && !said && !undo) return <></>;
  const card = q ? cardOf(q) : null;
  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>
        {w.heading}
        {open.length > 1 ? ` (${w.waiting(open.length)})` : ""}
      </h3>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? said.text : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? said.text : ""}
      </p>
      {undo && (
        <div class="row">
          <button type="button" class="button secondary" disabled={busy} onClick={() => void takeBack()}>
            {w.undo}
          </button>
        </div>
      )}
      {q && card && (
        <>
          <p>{card.said}</p>
          <p>
            <strong>{card.ask}</strong>
          </p>
          <p class="small">
            {w.no}: {card.noMeans} {w.both}: {card.bothMeans} {w.notNowMeans}
          </p>
          <div class="row">
            <button type="button" class="button" disabled={busy} onClick={() => void answer(q, "no")}>
              {w.no}
            </button>
            <button type="button" class="button secondary" disabled={busy} onClick={() => void answer(q, "both")}>
              {w.both}
            </button>
            <button type="button" class="button secondary" disabled={busy} onClick={() => setSkipped((was) => new Set(was).add(key(q)))}>
              {w.notNow}
            </button>
          </div>
          <SavingWait active={busy} stage={w.saving} />
        </>
      )}
    </section>
  );
}
