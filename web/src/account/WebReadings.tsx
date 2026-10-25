/** @jsxImportSource preact */
// "What people believe" on the Story graph (#2412 slice 3b; core PR 2693): the theories, accusations, reveals
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
type Sending = { stage: string; since: number; movedAt: number; stalled: boolean; moveKey: string };

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
      if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "not-allowed")) setList(null);
      else setFailed(true);
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
    setSending({ stage: "queued", since: now, movedAt: now, stalled: false, moveKey: "" });
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
            }
          : was,
      );
    };
    try {
      const got = await api.answerReading(campaignRef, answer, onLook);
      setSaid({ ok: true, text: ok, n: ++counter.current });
      if (answer.action === "place" && got.undoable) setUndo({ answerId: got.answerId });
    } catch (error) {
      setSaid({ ok: false, text: errorWords(error), n: ++counter.current });
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

      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      {sending && (
        <>
          <WorkProgress
            stage={w.stages[sending.stage] ?? w.stages["queued"] ?? ""}
            startedAt={sending.since}
            updatedAt={sending.movedAt}
            serverNow={Math.floor(Date.now() / 1000)}
            state={sending.stalled ? "stalled" : "running"}
          />
          {sending.stalled && <p class="small">{w.stalledWhat}</p>}
        </>
      )}
      {undo && !busy && (
        <p class="row">
          <button type="button" class="button secondary" onClick={() => void send({ action: "undo", id: undo.answerId }, w.done.undo)}>
            {w.undo}
          </button>
        </p>
      )}

      {list.readings.map((r) => (
        <div key={r.id} class="panel">
          <p class="small muted">{w.rules[r.rule] ?? w.rules["theory"]}</p>
          <p id={`wr-${r.id}`}>{r.statement}</p>
          <p class="small">
            {w.believedBy(r.who)}. {w.sure(r.sureness)}.
            {r.sessionAt > 0 && <> {w.saidAt(sessionDay(r.sessionAt))}</>}
          </p>
          {!r.canPlace && <p class="small">{w.waitingOn(r.waitingOn)}</p>}
          <div class="row">
            {r.canPlace && (
              <button
                type="button"
                class="button"
                disabled={busy}
                aria-describedby={`wr-${r.id}`}
                onClick={() => void send({ action: "place", id: r.id }, w.done.place)}
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
