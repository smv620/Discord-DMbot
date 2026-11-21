/** @jsxImportSource preact */
// "Let me choose" on the join question (#4841, #4799 slice 2, the page half of #4891): the DM picks a session or a story thread to
// catch the new character up from, one pick at a time, each with its own Undo. Opening it asks the bot to work the choices out and
// waits; the lists are read when they are kept. Its own file, loaded only from the join card while PARTY_JOIN_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { joinChooseWords as w } from "../content/join-choose-words";
import { ApiError, type AccountApi, type JoinChoices } from "./api";
import SavingWait from "./SavingWait";

type Phase = "working" | "ready" | "failed";
type Undo = { batch: number; first: boolean; names: string[] };

/** The plain line for a pick or an Undo that did not go through, by the bot's word for it. */
function sayFailure(error: unknown, undoing: boolean): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "gone") return w.answered;
  if (error.kind === "nothing-left") return w.couldNot;
  if (undoing && error.kind === "undo-late") return w.tooLate;
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
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [undo, setUndo] = useState<Undo | null>(null);
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  // Asks the bot and waits; the seconds so far are shown while it works (#4891).
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

  // The choices are read again after each pick or Undo: the counts change (#4891).
  async function reread(): Promise<void> {
    if (!api.joinChoices) return;
    try {
      const got = await api.joinChoices(campaignRef, group);
      if (live.current) setChoices(got);
    } catch {
      if (live.current) setPhase("failed");
    }
  }

  // One press in flight at a time.
  async function run(task: () => Promise<void>, undoing = false): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await task();
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error, undoing) });
    } finally {
      await reread();
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  const who = w.join(names);
  const know = names.length === 1 ? "knows" : "know";

  // Pick: a session (by its time) or a story thread (by its id).
  const pick = (target: { session: number } | { thread: string }, source: string) =>
    run(async () => {
      if (!api.joinChoose || !choices?.choices) return;
      const got = await api.joinChoose(campaignRef, group, target);
      if (!live.current) return;
      const members = choices.choices.members;
      const chose = got.chose.flatMap((c) => {
        const matched = members.filter((m) => m.id.startsWith(c.id));
        return matched.length === 1 && matched[0] ? [{ name: matched[0].name, count: c.count }] : [];
      });
      const first = chose[0];
      let text = "";
      if (chose.length === 1 && first) text = w.doneOne(first.name, first.count, source);
      else if (chose.length > 1 && first) text = w.doneMany(`${first.name} now knows ${first.count} more ${first.count === 1 ? "thing" : "things"}`, chose.slice(1).map((c) => `${c.name} ${c.count}`), source);
      if (got.choseMore > 0) text = `${text} ${w.moreGiven(got.choseMore)}`;
      setSaid({ ok: true, text: text.trim() });
      if (got.batch !== null) setUndo({ batch: got.batch, first: got.first, names: chose.map((c) => c.name) });
    });

  const undoPick = (u: Undo) =>
    run(async () => {
      await api.answerJoin!(campaignRef, { action: "undo", batch: u.batch });
      if (!live.current) return;
      setUndo(null);
      setSaid({ ok: true, text: u.first ? w.undoneFirst(who) : w.undonePick(who, know) });
    }, true);

  if (!api.askJoinChoices || !api.joinChoices) return null;
  const sessions = choices?.choices?.sessions ?? [];
  const threads = choices?.choices?.threads ?? [];
  const members = choices?.choices?.members ?? [];
  const countLine = (counts: Record<string, number>): string => {
    const parts = members.map((m) => ({ name: m.name, n: counts[m.id] ?? 0 }));
    if (parts.length === 1 && parts[0]) return w.countOne(parts[0].n, parts[0].name);
    return w.countMany(parts.map((p) => `${p.name} ${p.n}`).join(" · "));
  };

  return (
    <section class="stack" aria-label={w.button}>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status" tabIndex={-1}>{said?.ok ? said.text : ""}</p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">{said && !said.ok ? said.text : ""}</p>

      {phase === "working" && (
        <p class="muted" role="status">
          {w.working} {w.secondsSoFar(seconds)} {seconds >= 20 ? w.stillWorking : ""}
        </p>
      )}
      {phase === "failed" && (
        <div class="stack">
          <p class="warn" role="alert">{w.failed}</p>
          <button type="button" class="button secondary" onClick={() => void ask()}>{w.lookAgain}</button>
        </div>
      )}

      {phase === "ready" && choices && (
        <>
          {choices.madeAt !== null && (
            <p class="small muted">
              {w.workedOut(agoWords(choices.madeAt))}{" "}
              <button type="button" class="button secondary" disabled={busy} onClick={() => void ask()}>{w.lookAgain}</button>
            </p>
          )}
          {choices.choices === null ? (
            <button type="button" class="button secondary" disabled={busy} onClick={() => void ask()}>{w.lookAgain}</button>
          ) : choices.choices.nobody ? (
            <p class="small">{w.nobody(who)}</p>
          ) : sessions.length === 0 && threads.length === 0 ? (
            <p class="small">{w.nothingAtAll(who, know)}</p>
          ) : (
            <>
              <p>
                <strong>{w.head(who, know)}</strong>{" "}
                {back
                  ? names.length === 1
                    ? w.untilBackOne(who)
                    : w.untilBackMany
                  : names.length === 1
                    ? w.untilOne(who)
                    : w.untilMany}
              </p>
              <p class="small">{w.each(who)}</p>
              {choices.choices.sessionsInAll > sessions.length && <p class="small muted">{w.onlyLast}</p>}
              {choices.choices.threadsInAll > threads.length && <p class="small muted">{w.threadsCut}</p>}
              <p class="small muted">{w.lore}</p>

              {sessions.length > 0 && (
                <section class="stack" aria-label={w.bySession}>
                  <h4>{w.bySession}</h4>
                  <ul class="stack">
                    {sessions.map((s) => (
                      <li key={s.at} class="row">
                        <span>
                          <strong>{w.sessionLabel(s.number)}</strong> {countLine(s.counts)}
                        </span>
                        <button type="button" class="button secondary" disabled={busy} onClick={() => void pick({ session: s.at }, `session ${s.number}`)}>
                          {w.give}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {threads.length > 0 && (
                <section class="stack" aria-label={w.byThread}>
                  <h4>{w.byThread}</h4>
                  <ul class="stack">
                    {threads.map((t) => (
                      <li key={t.id} class="row">
                        <span>
                          <strong>{t.name}</strong> {countLine(t.counts)}
                        </span>
                        <button type="button" class="button secondary" disabled={busy} onClick={() => void pick({ thread: t.id }, `the story thread ${t.name}`)}>
                          {w.give}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </>
      )}

      {undo && (
        <div class="stack">
          <div class="row">
            <button type="button" class="button secondary" disabled={busy} onClick={() => void undoPick(undo)}>{w.undo}</button>
          </div>
          <p class="small muted">{w.undoLine}</p>
        </div>
      )}
      <div class="row">
        <button type="button" class="button secondary" onClick={onClose}>{w.close}</button>
      </div>
      <SavingWait active={busy} stage={w.saving} />
    </section>
  );
}
