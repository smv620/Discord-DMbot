/** @jsxImportSource preact */
// The "Still in the party?" question on the Questions page (#4895, party knowledge 6a): a character marked dead, or a goodbye heard at the
// table, answered as in Discord's 🧭 card: "Left the party" (with ↩️ Undo, which asks the question again) or "Still in the party". Each
// answer removes only that character's row. DM only: the door answers anyone else with nothing. Nothing shows while PARTY_LEAVES_LIVE is off.
import { useEffect, useRef, useState } from "preact/hooks";

import { leaveWords as w } from "../content/leave-questions-words";
import { PARTY_LEAVES_LIVE } from "../content/party-leaves-switch";
import { ApiError, type AccountApi, type LeaveGroup } from "./api";
import SavingWait from "./SavingWait";

type Said = { ok: boolean; text: string };
// The last "Left the party" with its change number, so Undo can take it back.
type Undo = { batch: number; name: string };

/** The plain line for an answer that did not go through, by the bot's error word. `undoName` is set for an Undo, and names its character. */
function sayFailure(error: unknown, undoName: string | null): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "gone") return w.gone;
  if (error.kind === "still-waiting") return w.unsure;
  // The bot's own words for an Undo that is too late or whose question changed (waitForAnswer maps them to these kinds).
  if (undoName !== null && (error.kind === "undo-late" || error.kind === "changed")) return w.tooLate(undoName);
  return w.couldNot;
}

export default function LeaveQuestions({
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
  const [groups, setGroups] = useState<LeaveGroup[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  // The row being answered (a character id), or "undo" while an Undo is with the bot.
  const [busy, setBusy] = useState<string | null>(null);
  const [undo, setUndo] = useState<Undo | null>(null);
  // Groups set aside with "Later" stay hidden for the life of the card (until the page is left or reloaded).
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
    if (!PARTY_LEAVES_LIVE || !api.leaveQuestions) return;
    try {
      const got = await api.leaveQuestions(campaignRef);
      if (!live.current) return;
      setGroups(got);
      setFailed(false);
      onLeft?.(got.reduce((n, g) => n + g.characters.length, 0));
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

  async function press(key: string, run: () => Promise<void>, undoName: string | null = null): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(key);
    setSaid(null);
    try {
      await run();
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error, undoName) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      if (live.current) setBusy(null);
    }
  }

  const answer = (c: { id: string; name: string }, choice: "left" | "stays") => {
    // A new answer takes back the Undo of the last one: it would revert a different character now.
    setUndo(null);
    return press(c.id, async () => {
      const got = await api.answerLeave!(campaignRef, { character: c.id, answer: choice });
      if (!live.current) return;
      if (choice === "left") {
        setUndo(got.batch === null ? null : { batch: got.batch, name: c.name });
        setSaid({ ok: true, text: w.leftDone(c.name) });
      } else {
        setSaid({ ok: true, text: w.stayedDone(c.name) });
      }
    });
  };

  const takeBack = (u: Undo) =>
    press(
      "undo",
      async () => {
        await api.answerJoin!(campaignRef, { action: "undo", batch: u.batch });
        if (live.current) {
          setUndo(null);
          setSaid({ ok: true, text: w.undone(u.name) });
        }
      },
      u.name,
    );

  const setAside = (g: LeaveGroup) => {
    setLater((was) => new Set(was).add(g.group));
    setSaid({ ok: true, text: w.laterDone });
    // The walk counts what is still shown, so a part set aside with Later can finish (#4740).
    onLeft?.(shown.filter((x) => x.group !== g.group).reduce((n, x) => n + x.characters.length, 0));
  };

  if (!PARTY_LEAVES_LIVE || !api.leaveQuestions || !api.answerLeave) return null;
  if (failed && !groups) {
    return (
      <div id={id} class="stack">
        <p class="warn" role="alert">{w.failed}</p>
        <button type="button" class="button secondary" onClick={() => void read()}>
          {w.tryAgain}
        </button>
      </div>
    );
  }
  if (!groups) return <p class="muted" role="status">{w.loading}</p>;
  const refreshNotice = failed ? (
    <p class="warn" role="alert">
      {w.refreshFailed}{" "}
      <button type="button" class="button secondary" onClick={() => void read()}>{w.tryAgain}</button>
    </p>
  ) : null;
  // Empty groups are not drawn; a group set aside with Later is hidden until the list is read again.
  const shown = groups.filter((g) => g.characters.length > 0 && !later.has(g.group));

  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>{w.title}</h3>
      {said && (
        <p
          ref={statusRef}
          tabIndex={-1}
          role={said.ok ? "status" : "alert"}
          class={said.ok ? "ok" : "warn"}
        >
          {said.text}
        </p>
      )}
      {refreshNotice}
      {shown.map((g) => (
        <section key={g.group} class="stack" aria-label={`${w.title}: ${g.characters.map((c) => c.name).join(", ")}`}>
          {g.characters.map((c) => {
            const questionId = `${id}-${c.id}-q`;
            const many = g.characters.length > 1;
            return (
              <div key={c.id} class="stack">
                <p id={questionId}>{g.kind === "died" ? w.died(c.name) : w.leave(c.name)}</p>
                <div class="row">
                  <button
                    type="button"
                    class="button secondary"
                    aria-describedby={questionId}
                    disabled={busy !== null}
                    onClick={() => void answer(c, "left")}
                  >
                    {many ? w.shortLeft(c.name) : w.left}
                  </button>
                  <button
                    type="button"
                    class="button secondary"
                    aria-describedby={questionId}
                    disabled={busy !== null}
                    onClick={() => void answer(c, "stays")}
                  >
                    {many ? w.shortStays(c.name) : w.stays}
                  </button>
                </div>
                <SavingWait active={busy === c.id} stage={w.saving} />
              </div>
            );
          })}
          <div class="row">
            <button
              type="button"
              class="button secondary"
              aria-label={`${w.later}: ${g.characters.map((c) => c.name).join(", ")}`}
              disabled={busy !== null}
              onClick={() => setAside(g)}
            >
              {w.later}
            </button>
          </div>
        </section>
      ))}
      {undo && api.answerJoin && (
        <div class="stack">
          <button type="button" class="button secondary" disabled={busy !== null} onClick={() => void takeBack(undo)}>
            {w.undo}
          </button>
        </div>
      )}
      <SavingWait active={busy === "undo"} stage={w.saving} />
    </section>
  );
}
