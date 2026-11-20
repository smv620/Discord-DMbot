/** @jsxImportSource preact */
// The 🧵 Progress to check card on the Names page (#4616): what a session's talk changed on the story threads the campaign keeps,
// one thread at a time. The DM unticks what didn't happen, then Keeps these or None happened; each press is one change for the bot,
// and Undo takes it back. Level with Discord's card. Its own file, fetched only for a DM while THREADS_LIVE. No id is shown.
import { useEffect, useRef, useState } from "preact/hooks";

import { threadProgressWords as w } from "../content/threads-words";
import { ApiError, type AccountApi, type ThreadProgressThread } from "./api";
import SavingWait from "./SavingWait";

type Said = { ok: boolean; text: string };
// The last press that can be taken back: a keep with a batch, or a turn-down (keep with nothing ticked, or none) with its lines.
type Undoable = { kind: "batch"; batch: number; name: string } | { kind: "lines"; lines: string[]; name: string } | null;

const dateOf = (at: number): string => new Date(at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });

/** Where the talk was heard: one session (with its date when there is one), or several. */
function whereOf(t: ThreadProgressThread): string {
  return t.sessions === 1 ? w.oneSession(t.sessionAt === null ? null : dateOf(t.sessionAt)) : w.sessions(t.sessions);
}

export default function ThreadProgress({ api, refId, onSignedOut }: { api: AccountApi; refId: string; onSignedOut?: () => void }) {
  const [list, setList] = useState<ThreadProgressThread[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [shown, setShown] = useState(0);
  const [unticked, setUnticked] = useState<Set<string>>(new Set());
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const [undoable, setUndoable] = useState<Undoable>(null);
  const flight = useRef(false);

  async function load(): Promise<void> {
    if (!api.threadProgress) return;
    try {
      setList(await api.threadProgress(refId));
      setProblem(null);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      setProblem(w.wrong);
    }
  }
  useEffect(() => {
    void load();
  }, [api, refId]);

  const threads = list ?? [];
  const current = threads[Math.min(shown, Math.max(threads.length - 1, 0))];

  /** One press: queued for the bot, the list read again after it, and the press's own line or its failure's. */
  async function press(
    what: "keep" | "none" | "undo",
    run: () => Promise<{ batch: number | null }>,
    then: (made: { batch: number | null }) => { said: Said; next: Undoable },
  ): Promise<void> {
    if (flight.current || !api.changeThreadProgress) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      const made = await run();
      const out = then(made);
      setSaid(out.said);
      setUndoable(out.next);
      setUnticked(new Set());
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      // A word from the bot says what it was: gone (answered meanwhile), changed (the thread moved on, so a keep or an Undo is off).
      const gone = error instanceof ApiError && (error.kind === "gone" || error.note === "gone");
      const changed = error instanceof ApiError && error.note === "changed";
      const text = gone ? w.gone : changed && what === "keep" ? w.changedKeep : changed && what === "undo" ? w.tooLate : w.wrong;
      setSaid({ ok: false, text });
      setUndoable(null); // a press that failed takes back nothing
    } finally {
      await load();
      flight.current = false;
      setBusy(false);
    }
  }

  function keep(t: ThreadProgressThread): void {
    const on = t.lines.filter((l) => !unticked.has(l.id)).map((l) => l.id);
    const off = t.lines.filter((l) => unticked.has(l.id)).map((l) => l.id);
    void press(
      "keep",
      () => api.changeThreadProgress!(refId, { action: "keep", on, off }),
      (made) => {
        if (made.batch !== null) return { said: { ok: true, text: w.kept(on.length, t.name) }, next: { kind: "batch", batch: made.batch, name: t.name } };
        return { said: { ok: true, text: w.leftAsIs(t.name) }, next: { kind: "lines", lines: off, name: t.name } };
      },
    );
  }

  function none(t: ThreadProgressThread): void {
    const lines = t.lines.map((l) => l.id);
    void press(
      "none",
      () => api.changeThreadProgress!(refId, { action: "none", lines }),
      () => ({ said: { ok: true, text: w.leftAsIs(t.name) }, next: { kind: "lines", lines, name: t.name } }),
    );
  }

  function takeBack(u: NonNullable<Undoable>): void {
    void press(
      "undo",
      () => api.changeThreadProgress!(refId, u.kind === "batch" ? { action: "undo", batch: u.batch } : { action: "undo", lines: u.lines }),
      () => ({ said: { ok: true, text: w.undone(u.name) }, next: null }),
    );
  }

  function toggle(id: string): void {
    setUnticked((was) => {
      const next = new Set(was);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (problem && !list) return <p class="warn" role="alert">{problem}</p>;
  if (!list || !current || current.lines.length === 0) return null; // nothing waits: no empty box

  return (
    <section class="stack" aria-labelledby="thread-progress-heading">
      <h3 id="thread-progress-heading">
        {w.heading}
        {threads.length > 1 ? w.thread(Math.min(shown, threads.length - 1) + 1, threads.length) : ""}
      </h3>
      <p class="small">{w.heard(current.name, whereOf(current))}</p>
      <div class="stack" role="group" aria-label={w.heading}>
        {current.lines.map((l) => (
          <label class="choice" key={l.id}>
            <input type="checkbox" checked={!unticked.has(l.id)} disabled={busy} onChange={() => toggle(l.id)} />
            <span>{l.text}</span>
          </label>
        ))}
      </div>
      {current.more > 0 && <p class="small">{w.more(current.more)}</p>}
      <p class="small">{w.help}</p>
      <div class="row">
        <button type="button" class="button" disabled={busy} onClick={() => keep(current)}>
          {w.keep}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={() => none(current)}>
          {w.none}
        </button>
        {threads.length > 1 && (
          <button type="button" class="button secondary" disabled={busy} onClick={() => setShown((s) => (s + 1) % threads.length)}>
            {w.skip}
          </button>
        )}
      </div>
      <SavingWait active={busy} stage={w.saving} />
      {said && (
        <p class={said.ok ? "ok" : "warn"} role={said.ok ? "status" : "alert"}>
          {said.text}
        </p>
      )}
      {undoable && (
        <div class="row">
          <button type="button" class="button secondary" disabled={busy} onClick={() => takeBack(undoable)}>
            {w.undo}
          </button>
        </div>
      )}
    </section>
  );
}
