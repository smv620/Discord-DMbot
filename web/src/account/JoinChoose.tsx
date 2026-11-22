/** @jsxImportSource preact */
// "Let me choose" on the join question (#4841, #4799 slice 2, the page half of #4891): the DM picks a session or a story thread to
// catch the new character up from, each pick with its own Undo. Opening it asks the bot to work the choices out and waits; the lists
// are read when they are kept. The parent keeps this card open even when the question leaves the list (after a pick), so its picks
// and their Undos stay reachable until the DM closes it. Its own file, loaded only from the join card while PARTY_JOIN_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { joinChooseWords as w } from "../content/join-choose-words";
import { joinWords } from "../content/join-questions-words";
import { ApiError, type AccountApi, type JoinChoices } from "./api";
import SavingWait from "./SavingWait";

type Phase = "working" | "ready" | "failed";
/** One pick on the card: its own line and, while it can still be undone, its own Undo (#4891). */
type Pick = { key: number; text: string; batch: number | null; first: boolean; state: "done" | "undone" | "late" };

let nextKey = 1;

/** The line for a pick that did not go through, by the bot's word for it (the join card's own words for the too-late case). */
function pickProblem(error: unknown, who: string, know: string, source: string): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "gone") return w.answered;
  if (error.kind === "nothing-left") return w.nothingLeft(who, know, source);
  if (error.kind === "undo-late" || error.kind === "changed") return joinWords.tooLate;
  return w.couldNot;
}

/** How long ago the choices were worked out, in words. */
function agoWords(madeAt: number | null): string {
  if (madeAt === null) return w.minutesAgo(0);
  return w.minutesAgo(Math.floor(Math.max(0, Date.now() / 1000 - madeAt) / 60));
}

export default function JoinChoose({
  api,
  campaignRef,
  group,
  names,
  back,
  onClose,
}: {
  api: AccountApi;
  campaignRef: string;
  group: string;
  names: string[];
  back: boolean;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("working");
  const [seconds, setSeconds] = useState(0);
  const [choices, setChoices] = useState<JoinChoices | null>(null);
  const [stale, setStale] = useState(false);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [focusKey, setFocusKey] = useState<number | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const live = useRef(true);
  const heading = useRef<HTMLHeadingElement>(null);
  const doneLines = useRef(new Map<number, HTMLParagraphElement>());
  useEffect(() => {
    live.current = true;
    heading.current?.focus(); // focus moves into the card when it opens (#4891)
    return () => void (live.current = false);
  }, []);

  // Asks the bot and waits; the seconds so far are shown while it works.
  async function ask(): Promise<void> {
    if (!api.askJoinChoices || !api.joinChoices) return;
    setPhase("working");
    setSeconds(0);
    const tick = setInterval(() => setSeconds((was) => was + 1), 1000);
    try {
      await api.askJoinChoices(campaignRef, group);
      const got = await api.joinChoices(campaignRef, group);
      if (live.current) {
        setChoices(got);
        setStale(false);
        setPhase("ready");
      }
    } catch {
      if (live.current) setPhase("failed");
    } finally {
      clearInterval(tick);
    }
  }
  useEffect(() => {
    void ask();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef, group]);

  // The choices are read again after each pick or Undo: the counts change. A read that fails keeps the list and says so (#4891).
  async function reread(): Promise<void> {
    if (!api.joinChoices) return;
    try {
      const got = await api.joinChoices(campaignRef, group);
      if (live.current) {
        setChoices(got);
        setStale(false);
      }
    } catch {
      if (live.current) setStale(true);
    }
  }

  // One press in flight at a time.
  async function run(task: () => Promise<void>): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setAlert(null);
    try {
      await task();
    } finally {
      await reread();
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  const who = joinWords.join(names);
  const know = names.length === 1 ? "knows" : "know";
  const members = choices?.choices?.members ?? [];
  const sessions = choices?.choices?.sessions ?? [];
  const threads = choices?.choices?.threads ?? [];

  // Pick: a session (by its time) or a story thread (by its id); the pick keeps its own line and Undo.
  const pick = (target: { session: number } | { thread: string }, source: string) =>
    run(async () => {
      if (!api.joinChoose) return;
      try {
        const got = await api.joinChoose(campaignRef, group, target);
        if (!live.current) return;
        // A character is named by the first eight characters of its id; an id matching none, or several, is left out.
        const chose = got.chose.flatMap((c) => {
          const matched = members.filter((m) => m.id.startsWith(c.id));
          return matched.length === 1 && matched[0] ? [{ name: matched[0].name, count: c.count }] : [];
        });
        const first = chose[0];
        let text: string;
        if (!first) text = w.doneGeneric;
        else if (chose.length === 1) text = w.doneOne(first.name, first.count, source);
        else text = w.doneMany(w.doneManyFirst(first.name, first.count), chose.slice(1).map((c) => `${c.name} ${c.count}`), source);
        if (got.choseMore > 0) text = `${text} ${w.moreGiven(got.choseMore)}`;
        const key = nextKey++;
        setPicks((was) => [...was, { key, text, batch: got.batch, first: got.first, state: "done" }]);
        setFocusKey(key);
      } catch (error) {
        if (live.current) setAlert(pickProblem(error, who, know, source));
      }
    });

  // Undo of one pick: its own line says what it became. The question is read again to tell a first pick from a later one.
  const undoPick = (p: Pick) =>
    run(async () => {
      if (!api.answerJoin || p.batch === null) return;
      try {
        await api.answerJoin(campaignRef, { action: "undo", batch: p.batch });
        let waitingAgain = false;
        if (p.first && api.joinQuestions) {
          const groups = await api.joinQuestions(campaignRef);
          waitingAgain = groups.some((x) => x.group === group && x.answer === "waiting");
        }
        if (live.current)
          setPicks((was) =>
            was.map((x) => (x.key === p.key ? { ...x, state: "undone", text: p.first && waitingAgain ? (back ? joinWords.backUndone(names) : w.undoneFirst(who)) : w.undonePick(who, know) } : x)),
          );
      } catch (error) {
        // A gone question or a late Undo: this pick can't be undone any more, and the card says so (#4891).
        const late = error instanceof ApiError && (error.kind === "gone" || error.kind === "undo-late" || error.kind === "changed");
        if (live.current && late) setPicks((was) => was.map((x) => (x.key === p.key ? { ...x, state: "late" } : x)));
        if (live.current) setAlert(late ? joinWords.tooLate : w.couldNot);
      }
    });

  // Focus follows the picks: a new pick's line takes focus; Close returns focus to the button that opened the card (in the parent).
  useEffect(() => {
    if (focusKey !== null) doneLines.current.get(focusKey)?.focus();
  }, [focusKey]);

  if (!api.askJoinChoices || !api.joinChoices) return null;
  const countLine = (counts: Record<string, number>): string => {
    const parts = members.map((m) => ({ name: m.name, n: counts[m.id] ?? 0 })).filter((p) => p.n > 0);
    if (parts.length === 0) return "";
    if (parts.length === 1 && parts[0]) return w.countOne(parts[0].n, parts[0].name);
    return w.countMany(parts.map((p) => `${p.name} ${p.n}`).join(" · "));
  };

  return (
    <section class="stack" aria-label={w.button}>
      <h3 tabIndex={-1} ref={heading}>{w.button}</h3>

      {picks.length > 0 && (
        <div class="stack" role="status">
          {picks.map((p) => (
            <div key={p.key} class="stack">
              <p class={p.state === "done" ? "ok" : "small muted"} tabIndex={-1} ref={(el) => {
                if (el) doneLines.current.set(p.key, el);
              }}>
                {p.text}
              </p>
              {p.state === "done" && p.batch !== null && (
                <div class="row">
                  <button type="button" class="button secondary" disabled={busy} onClick={() => void undoPick(p)}>{w.undo}</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {alert && (
        <p class="warn" role="alert">
          {alert}
        </p>
      )}

      {phase === "working" && (
        <div class="stack">
          <p class="muted" role="status">
            {w.working} {w.secondsSoFar(seconds)} {seconds >= 20 ? w.stillWorking : ""}
          </p>
          {seconds >= 20 && (
            <button type="button" class="button secondary" onClick={() => void ask()}>{w.lookAgain}</button>
          )}
        </div>
      )}
      {phase === "failed" && (
        <div class="stack">
          <p class="warn" role="alert">{w.failed}</p>
          <button type="button" class="button secondary" onClick={() => void ask()}>{w.lookAgain}</button>
        </div>
      )}

      {phase === "ready" && choices && (
        <>
          {stale && (
            <div class="stack">
              <p class="warn" role="status">{w.staleCounts}</p>
              <button type="button" class="button secondary" disabled={busy} onClick={() => void ask()}>{w.lookAgain}</button>
            </div>
          )}
          {choices.madeAt !== null && (
            <p class="small muted">
              {w.workedOut(agoWords(choices.madeAt))}{" "}
              <button type="button" class="button secondary" disabled={busy} onClick={() => void ask()}>{w.lookAgain}</button>
            </p>
          )}
          {choices.choices === null || choices.choices.nobody ? (
            choices.choices === null ? null : <p class="small">{w.nobody(who)}</p>
          ) : sessions.length === 0 && threads.length === 0 ? (
            <p class="small">{w.nothingAtAll(who, know)}</p>
          ) : (
            <>
              <p>
                <strong>{w.head(who, know)}</strong>{" "}
                {back ? (names.length === 1 ? w.untilBackOne(who) : w.untilBackMany) : names.length === 1 ? w.untilOne(who) : w.untilMany}
              </p>
              <p class="small">{w.each(who)}</p>
              {choices.choices.sessionsInAll > sessions.length && <p class="small muted">{w.onlyLast}</p>}
              {choices.choices.threadsInAll > threads.length && <p class="small muted">{w.threadsCut}</p>}
              <p class="small muted">{w.lore}</p>

              {sessions.length > 0 && (
                <section class="stack" aria-label={w.bySession}>
                  <h4>{w.bySession}</h4>
                  <ul class="stack">
                    {sessions.map((s) => {
                      const rowId = `join-choose-session-${s.at}`;
                      return (
                        <li key={s.at} class="row">
                          <span id={rowId}>
                            <strong>{w.sessionLabel(s.number)}</strong> {countLine(s.counts)}
                          </span>
                          <button type="button" class="button secondary" disabled={busy} aria-describedby={rowId} onClick={() => void pick({ session: s.at }, `session ${s.number}`)}>
                            {w.give}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              )}
              {threads.length > 0 && (
                <section class="stack" aria-label={w.byThread}>
                  <h4>{w.byThread}</h4>
                  <ul class="stack">
                    {threads.map((t) => {
                      const rowId = `join-choose-thread-${encodeURIComponent(t.id)}`;
                      return (
                        <li key={t.id} class="row">
                          <span id={rowId}>
                            <strong>{t.name}</strong> {countLine(t.counts)}
                          </span>
                          <button type="button" class="button secondary" disabled={busy} aria-describedby={rowId} onClick={() => void pick({ thread: t.id }, `the story thread ${t.name}`)}>
                            {w.give}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              )}
            </>
          )}
        </>
      )}

      <div class="row">
        <button type="button" class="button secondary" onClick={onClose}>{w.close}</button>
      </div>
      <SavingWait active={busy} stage={w.saving} />
    </section>
  );
}
