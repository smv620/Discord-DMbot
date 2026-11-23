/** @jsxImportSource preact */
// "Deeds to record" on the Questions page (#3591, page half): one deed at a time, Record, Not this, Later, and an Undo for a recorded
// one, in Discord's words. DM only: the door answers anyone else with nothing. Nothing is read or shown while DEED_QUESTIONS_LIVE
// is off, and no deed's words leave this card (not the title, a URL or a console line).
import { useEffect, useRef, useState } from "preact/hooks";

import { DEED_QUESTIONS_LIVE } from "../content/deed-questions-switch";
import { deedWords as w } from "../content/deed-questions-words";
import { ApiError, type AccountApi, type DeedWaiting } from "./api";
import SavingWait from "./SavingWait";

type Said = { ok: boolean; text: string };
// The last recorded deed with its change number, so Undo can take it back.
type Undo = { batch: number; text: string };

/** The plain line for an answer that did not go through, by the bot's error word. */
function sayFailure(error: unknown, undoing: boolean): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "gone") return w.gone;
  if (error.kind === "refused") return w.refused;
  if (undoing && (error.kind === "undo-late" || error.kind === "changed")) return w.tooLate;
  return w.couldNot;
}

export default function DeedQuestions({
  api,
  campaignRef,
  id,
  onLeft,
}: {
  api: AccountApi;
  campaignRef: string;
  id: string;
  onLeft?: (left: number) => void;
}) {
  const [deeds, setDeeds] = useState<DeedWaiting[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  // "record", "not" or "undo" while an answer is with the bot; null otherwise.
  const [busy, setBusy] = useState<string | null>(null);
  const [undo, setUndo] = useState<Undo | null>(null);
  // Deeds set aside with Later stay hidden until the card is read again (the next one shows).
  const [later, setLater] = useState<Set<string>>(new Set());
  const statusRef = useRef<HTMLParagraphElement | null>(null);
  const flight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    // Nothing is read while the switch is off: the page makes no call for it.
    if (!DEED_QUESTIONS_LIVE || !api.deedQuestions) return;
    try {
      const got = await api.deedQuestions(campaignRef);
      if (!live.current) return;
      setDeeds(got.deeds);
      setFailed(false);
      onLeft?.(got.total);
      setLater(new Set());
    } catch {
      if (live.current) setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  // Focus moves to the line that says what happened, so a screen reader reads it at once.
  useEffect(() => {
    if (said) statusRef.current?.focus();
  }, [said]);

  async function press(key: string, run: () => Promise<void>, undoing = false): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(key);
    setSaid(null);
    try {
      await run();
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error, undoing) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      if (live.current) setBusy(null);
    }
  }

  const answer = (d: DeedWaiting, choice: "record" | "not") => {
    // A new answer takes back the Undo of the last one: it would revert a different deed now.
    setUndo(null);
    return press(choice, async () => {
      const got = await api.answerDeed!(campaignRef, { deed: d.id, answer: choice });
      if (!live.current) return;
      if (choice === "record") {
        setUndo(got.batch === null ? null : { batch: got.batch, text: d.text });
        setSaid({ ok: true, text: w.recorded });
      } else {
        setSaid({ ok: true, text: w.notThis });
      }
    });
  };

  const takeBack = (u: Undo) =>
    press(
      "undo",
      async () => {
        await api.answerDeed!(campaignRef, { undo: u.batch });
        if (live.current) {
          setUndo(null);
          setSaid({ ok: true, text: w.undone });
        }
      },
      true,
    );

  if (!DEED_QUESTIONS_LIVE || !api.deedQuestions || !api.answerDeed) return null;
  if (failed && !deeds) {
    return (
      <div id={id} class="stack">
        <p class="warn" role="alert">{w.failed}</p>
        <button type="button" class="button secondary" onClick={() => void read()}>
          {w.tryAgain}
        </button>
      </div>
    );
  }
  if (!deeds) return <p class="muted" role="status">{w.loading}</p>;
  // The first deed not set aside with Later is the one shown; the rest are counted.
  const open = deeds.filter((d) => !later.has(d.id));
  const first = open[0];
  const landed = first ? (w.landed[first.effect] ?? w.landedDefault) : "";

  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>{w.title}</h3>
      {said && (
        <p ref={statusRef} tabIndex={-1} role={said.ok ? "status" : "alert"} class={said.ok ? "ok" : "warn"}>
          {said.text}
        </p>
      )}
      {!first && <p role="status">{w.empty}</p>}
      {first && (
        <div class="stack">
          <p>
            <strong>{first.doer}</strong>: “{first.text}”{w.kindNote[first.kind] ?? ""}
          </p>
          <p id={`${id}-question`}>
            <span>It mattered to </span>
            <strong>{first.viewers.join(", ")}</strong>
            <span> (it {landed} them). Record it?</span>
          </p>
          {open.length > 1 && <p class="small muted">{w.more(open.length - 1)}</p>}
          <p class="small muted">{w.nothingSaved}</p>
          <div class="row">
            <button type="button" class="button" aria-describedby={`${id}-question`} disabled={busy !== null} onClick={() => void answer(first, "record")}>
              {w.record}
            </button>
            <button type="button" class="button secondary" aria-describedby={`${id}-question`} disabled={busy !== null} onClick={() => void answer(first, "not")}>
              {w.not}
            </button>
            <button
              type="button"
              class="button secondary"
              aria-describedby={`${id}-question`}
              disabled={busy !== null}
              onClick={() => {
                setLater((was) => new Set(was).add(first.id));
                setSaid(null);
                onLeft?.(open.length - 1);
              }}
            >
              {w.later}
            </button>
          </div>
          <SavingWait active={busy === "record" || busy === "not"} stage={w.saving} />
        </div>
      )}
      {undo && (
        <div class="stack">
          <p class="small">{w.changedMind}</p>
          <button type="button" class="button secondary" disabled={busy !== null} onClick={() => void takeBack(undo)}>
            {w.undo}
          </button>
        </div>
      )}
      <SavingWait active={busy === "undo"} stage={w.saving} />
    </section>
  );
}
