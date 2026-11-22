/** @jsxImportSource preact */
// "Things the characters learned" on the Questions page (#3863 step 2): what the characters learned, waiting to be kept, answered as in
// Discord's 💡 card: Keep (with ↩️ Undo, through the join undo) or Not this. Each answer removes only its own row. DM only: the door answers
// anyone else with nothing. Nothing is read while REVEALS_LIVE is off.
import { useEffect, useRef, useState } from "preact/hooks";

import { revealWords as w } from "../content/reveal-questions-words";
import { REVEALS_LIVE } from "../content/reveals-switch";
import { ApiError, type AccountApi, type RevealProposal } from "./api";
import SavingWait from "./SavingWait";

type Said = { ok: boolean; text: string };
// The last Keep with its change number, so Undo can take it back.
type Undo = { batch: number };

/** The plain line for an answer that did not go through, by the bot's error word. */
function sayFailure(error: unknown, undoing: boolean): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "gone") return w.gone;
  // The bot's wait ran out: it may have made the change, so say so rather than "nothing changed".
  if (error.kind === "still-waiting") return w.unsure;
  if (undoing && (error.kind === "undo-late" || error.kind === "changed")) return w.undoTooLate;
  return w.couldNot;
}

export default function RevealQuestions({
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
  const [items, setItems] = useState<RevealProposal[] | null>(null);
  const [failed, setFailed] = useState(false);
  // A player, or another campaign's DM, is refused: nothing is drawn, and it is not an error (#3863 step 2).
  const [hidden, setHidden] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  // The row being answered (a proposal id), or "undo" while an Undo is with the bot.
  const [busy, setBusy] = useState<string | null>(null);
  const [undo, setUndo] = useState<Undo | null>(null);
  const statusRef = useRef<HTMLParagraphElement | null>(null);
  const flight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    // Nothing is asked of the bot while the switch is off.
    if (!REVEALS_LIVE || !api.revealQuestions) return;
    try {
      const got = await api.revealQuestions(campaignRef);
      if (!live.current) return;
      setItems(got);
      setFailed(false);
      onLeft?.(got.length);
    } catch (error) {
      if (!live.current) return;
      if (error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found")) setHidden(true);
      else setFailed(true);
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

  const keep = (p: RevealProposal) =>
    press(p.id, async () => {
      // A new answer takes away the Undo of the last one.
      setUndo(null);
      const got = await api.answerReveal!(campaignRef, { id: p.id, answer: "keep" });
      if (!live.current) return;
      if (got.batch === null) {
        setSaid({ ok: true, text: w.alreadyKnown });
      } else {
        setUndo({ batch: got.batch });
        setSaid({ ok: true, text: w.kept });
      }
    });

  const notThis = (p: RevealProposal) =>
    press(p.id, async () => {
      setUndo(null);
      await api.answerReveal!(campaignRef, { id: p.id, answer: "not" });
      if (live.current) setSaid({ ok: true, text: w.okay });
    });

  const takeBack = (u: Undo) =>
    press(
      "undo",
      async () => {
        await api.answerJoin!(campaignRef, { action: "undo", batch: u.batch });
        if (live.current) {
          setUndo(null);
          setSaid({ ok: true, text: w.undone });
        }
      },
      true,
    );

  if (!REVEALS_LIVE || !api.revealQuestions || !api.answerReveal || hidden) return null;
  if (failed && !items) {
    return (
      <div id={id} class="stack">
        <p class="warn" role="alert">{w.failed}</p>
        <button type="button" class="button secondary" onClick={() => void read()}>
          {w.tryAgain}
        </button>
      </div>
    );
  }
  if (!items) {
    // The bot is asked first, so this can take a moment: the wait says it is working.
    return (
      <div id={id} class="stack">
        <p class="muted" role="status">{w.loading}</p>
        <SavingWait active stage={w.working} />
      </div>
    );
  }

  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>{w.title}</h3>
      <p class="small">{w.intro}</p>
      {said && (
        <p ref={statusRef} tabIndex={-1} role={said.ok ? "status" : "alert"} class={said.ok ? "ok" : "warn"}>
          {said.text}
        </p>
      )}
      {failed && (
        <p class="warn" role="alert">
          {w.refreshFailed}{" "}
          <button type="button" class="button secondary" onClick={() => void read()}>
            {w.tryAgain}
          </button>
        </p>
      )}
      {items.length === 0 ? (
        <p>{w.empty}</p>
      ) : (
        items.map((p) => {
          const textId = `${id}-${p.id}-text`;
          return (
            <div key={p.id} class="stack">
              <p id={textId}>{w.learned(p.who, p.what, p.how)}</p>
              {p.guessing && <p class="small">{w.guessing(p.who)}</p>}
              {p.player && <p class="small">{w.player(p.who)}</p>}
              <div class="row">
                <button type="button" class="button" aria-describedby={textId} disabled={busy !== null} onClick={() => void keep(p)}>
                  {w.keep}
                </button>
                <button type="button" class="button secondary" aria-describedby={textId} disabled={busy !== null} onClick={() => void notThis(p)}>
                  {w.notThis}
                </button>
              </div>
              <SavingWait active={busy === p.id} stage={w.working} />
            </div>
          );
        })
      )}
      {undo && api.answerJoin && (
        <div class="stack">
          <button type="button" class="button secondary" disabled={busy !== null} onClick={() => void takeBack(undo)}>
            {w.undo}
          </button>
        </div>
      )}
      <SavingWait active={busy === "undo"} stage={w.working} />
    </section>
  );
}
