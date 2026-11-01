/** @jsxImportSource preact */
// "What people believe" on the Story Graph (#2412 slice 3b; core PR 2693): the theories, accusations, reveals
// and tricks DMbot noticed, each of which the DM may keep as a belief (what someone thinks, never a fact),
// say is not right, or take back. Each answer is sent to DMbot, which makes the very calls the DM screen
// makes, so consent and every rule hold the same. Only a DM of the campaign gets the list (a player or a
// peek viewer gets "not found" and this renders nothing at all). It holds DMbot's own words only: names, a
// relationship, whose view and how sure, never a line or who said it. While an answer waits it shows the
// stage, how long it has waited and when it last moved (owner rule 2026-10-23). One answer at a time; the
// list is read again after each, so what shows is always the API's own.
import { useEffect, useRef, useState } from "preact/hooks";

import { readingWords as w } from "../content/web-readings";
import { ApiError, type AccountApi, type ReadingAnswer, type ReadingLook, type Readings } from "./api";
import WorkProgress from "./WorkProgress";

/** A session's day for a person ("17 October"), from Unix seconds, the same in every time zone. */
const sessionDay = (seconds: number): string =>
  new Date(seconds * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });

const errorWords = (error: unknown): string =>
  error instanceof ApiError ? (w.errors[error.kind] ?? (w.errors["other"] as string)) : (w.errors["other"] as string);

type Said = { ok: boolean; text: string; n: number } | null;
/** The wait as the page has seen it: the stage, when it began, and when it last moved (the page's own clock). */
type Sending = {
  stage: string;
  /** The page's clock when it began waiting, and when it last saw the wait move (used until the server's times can be placed). */
  since: number;
  movedAt: number;
  stalled: boolean;
  moveKey: string;
  /** The server's times, once known: when the answer was queued (the first "queued" look's last-moved time), how long it has
   * waited, and when it last moved. The server's own clock is then `queuedAt + waitedSeconds`. */
  queuedAt: number | null;
  waited: number;
  lastMovedAt: number;
};

export default function WebReadings({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<Readings | null>(null);
  const [failed, setFailed] = useState(false);
  const [sending, setSending] = useState<Sending | null>(null);
  const [said, setSaid] = useState<Said>(null);
  const [undo, setUndo] = useState<{ answerId: string } | null>(null); // the keep that can still be taken back
  const headingRef = useRef<HTMLHeadingElement>(null);
  const shown = useRef(campaignRef);
  const latest = useRef(0);
  const counter = useRef(0);
  const inFlight = useRef(false); // a second tap before the page redraws must not send a second answer

  async function read(): Promise<void> {
    if (!api.webReadings) return;
    const mine = ++latest.current;
    const asked = campaignRef;
    try {
      const got = await api.webReadings(asked);
      // (another campaign's list is never drawn here: the page may have moved on while this was asked)
      if (mine === latest.current && shown.current === asked) {
        setList(got);
        setFailed(false);
      }
    } catch (error) {
      if (mine !== latest.current) return;
      // A player, a peek viewer or another DM gets "not found": the part is simply not there.
      if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "not-allowed")) {
        setList(null);
        setFailed(false);
      } else setFailed(true);
    }
  }

  useEffect(() => {
    shown.current = campaignRef;
    setList(null);
    setSaid(null);
    setUndo(null);
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function send(answer: ReadingAnswer, ok: string): Promise<void> {
    if (!api.answerReading || inFlight.current) return;
    inFlight.current = true;
    const now = Math.floor(Date.now() / 1000);
    setSending({ stage: "queued", since: now, movedAt: now, stalled: false, moveKey: "", queuedAt: null, waited: 0, lastMovedAt: 0 });
    setSaid(null);
    setUndo(null);
    // What the bot says about the wait; "last moved" is when the page saw the stage or the move time change.
    const onLook = (look: ReadingLook): void => {
      if (!look.waits) return;
      const seen = Math.floor(Date.now() / 1000);
      const key = `${look.waits.stage}:${look.waits.lastMovedAt}`;
      setSending((was) =>
        was
          ? {
              ...was,
              stage: look.waits!.stage,
              stalled: look.waits!.stalled,
              movedAt: key !== was.moveKey ? seen : was.movedAt,
              moveKey: key,
              // While queued, "last moved" is when it was queued, which places the server's clock.
              queuedAt: look.waits!.stage === "queued" ? look.waits!.lastMovedAt : was.queuedAt,
              waited: look.waits!.waitedSeconds,
              lastMovedAt: look.waits!.lastMovedAt,
            }
          : was,
      );
    };
    try {
      const got = await api.answerReading(campaignRef, answer, onLook);
      setSaid({ ok: true, text: ok, n: ++counter.current });
      if (answer.action === "place" && got.undoable) setUndo({ answerId: got.answerId });
    } catch (error) {
      // "Gone" on an Undo means there is nothing left to take back, not that the belief isn't waiting.
      const text =
        answer.action === "undo" && error instanceof ApiError && error.kind === "gone" ? w.errors["undo-gone"] : errorWords(error);
      setSaid({ ok: false, text: text as string, n: ++counter.current });
    } finally {
      await read(); // (what shows is always the API's own, whatever happened)
      inFlight.current = false;
      setSending(null);
      headingRef.current?.focus(); // (the answered item has gone, and focus with it)
    }
  }

  if (!api.webReadings) return null;
  if (failed && !list)
    return (
      <section class="stack" aria-labelledby="readings-heading">
        <h3 id="readings-heading">{w.heading}</h3>
        <p class="warn" role="alert">
          {w.loadFailed}
        </p>
        <button type="button" class="button secondary" onClick={() => void read()}>
          {w.reload}
        </button>
      </section>
    );
  if (!list) return null;

  const busy = sending !== null;
  const total = list.counts.readings;
  return (
    <section class="stack web-readings" aria-labelledby="readings-heading">
      <h3 id="readings-heading" tabIndex={-1} ref={headingRef}>
        {w.heading}
      </h3>
      <p>
        <strong>{total === 0 ? w.none : w.count(total)}</strong>
      </p>
      <p class="small">{w.lead}</p>
      {list.capped && <p class="small">{w.more}</p>}
      {failed && (
        <p class="warn" role="alert">
          {w.loadFailed}{" "}
          <button type="button" class="button secondary" onClick={() => void read()}>
            {w.reload}
          </button>
        </p>
      )}

      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      {sending && (
        <>
          <p class="small">{w.oneAtATime}</p>
          {(() => {
            // The server's times when they can be placed (its clock is when it queued the answer plus how long it has
            // waited); until then the page's own clock, so "last moved" never claims more than the page saw.
            const known = sending.queuedAt !== null && sending.lastMovedAt > 0;
            const serverNow = known ? (sending.queuedAt as number) + sending.waited : Math.floor(Date.now() / 1000);
            return (
              <WorkProgress
                stage={w.stages[sending.stage] ?? w.stages["working"] ?? ""}
                startedAt={known ? serverNow - sending.waited : sending.since}
                updatedAt={known ? sending.lastMovedAt : sending.movedAt}
                serverNow={serverNow}
                state={sending.stalled ? "stalled" : "running"}
                whatToDo={w.stalledWhat}
              />
            );
          })()}
        </>
      )}
      {undo && !busy && (
        <div class="row">
          <button type="button" class="button secondary" onClick={() => void send({ action: "undo", id: undo.answerId }, w.done.undo)}>
            {w.undo}
          </button>
          <span class="small muted">{w.undoHint}</span>
        </div>
      )}

      {list.readings.map((r) => (
        <div key={r.id} class="panel">
          <p id={`wr-${r.id}`}>{r.statement}</p>
          <p class="small">
            {r.rule === "lie" ? w.lied(r.who, r.liar) : w.believedBy(r.who)}. {w.sure(r.sureness)}.
          </p>
          <p class="small muted">
            {w.rules[r.rule] ?? w.other}
            {r.sessionAt > 0 && <> · {w.saidAt(sessionDay(r.sessionAt))}</>}
          </p>
          {!r.canPlace && <p class="small">{w.waitingOn(r.waitingOn)}</p>}
          <div class="row">
            {r.canPlace && (
              <button
                type="button"
                class="button"
                disabled={busy}
                aria-describedby={`wr-${r.id}`}
                onClick={() => void send({ action: "place", id: r.id }, w.keptFor(r.who))}
              >
                {w.place}
              </button>
            )}
            <button
              type="button"
              class="button secondary"
              disabled={busy}
              aria-describedby={`wr-${r.id}`}
              onClick={() => void send({ action: "no", id: r.id }, w.done.no)}
            >
              {w.no}
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}
