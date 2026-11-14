/** @jsxImportSource preact */
// "DMbot's guesses" on the Names page (#3602): the matches DMbot made by itself, for the DM to say yes to or take
// back. DM only (the list is the DM's alone). Until the DM says yes, players and recaps still treat each pair as
// two names; the page says so. Each press is queued for the bot like every name action, and the wait shows its
// stage, how far along and how long it has run. Names and counts only: never a line of the transcript.
import { useEffect, useRef, useState } from "preact/hooks";

import { matchWords as w } from "../content/name-matches-words";
import { ApiError, type AccountApi, type MatchRow } from "./api";
import WorkProgress from "./WorkProgress";

type Load = { kind: "loading" } | { kind: "error" } | { kind: "ready"; matches: MatchRow[] };

const label = (b: { icon: string; text: string }) => (
  <>
    <span aria-hidden="true">{b.icon} </span>
    {b.text}
  </>
);

export default function NameMatches({ api, campaignRef, onChanged }: { api: AccountApi; campaignRef: string; onChanged?: () => void }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [run, setRun] = useState<{ done: number; total: number; since: number; moved: number } | null>(null);
  const [said, setSaid] = useState<string>("");
  const flight = useRef(false);
  const summary = useRef<HTMLParagraphElement>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  async function read(): Promise<void> {
    if (!api.nameMatches) return;
    try {
      const matches = await api.nameMatches(campaignRef);
      if (alive.current) setLoad({ kind: "ready", matches });
    } catch {
      if (alive.current) setLoad({ kind: "error" });
    }
  }
  useEffect(() => {
    void read();
  }, [api, campaignRef]);

  const reason = (error: unknown): string =>
    error instanceof ApiError ? (error.kind === "signed-out" ? w.signedOut : error.kind === "gone" || error.kind === "not-found" ? w.gone : w.other) : w.other;

  async function answer(todo: MatchRow[], what: "confirm" | "undo"): Promise<void> {
    if (flight.current || !api.answerMatch || todo.length === 0) return;
    flight.current = true;
    const since = Math.floor(Date.now() / 1000);
    setRun({ done: 0, total: todo.length, since, moved: since });
    setSaid("");
    let ok = 0;
    let problem = "";
    try {
      for (const m of todo) {
        if (!alive.current) break;
        try {
          await api.answerMatch(campaignRef, m.batch, what, () => {
            // Each look at the bot's answer is a sign it is still working: "last moved" follows it.
            const now = Math.floor(Date.now() / 1000);
            setRun((r) => (r ? { ...r, moved: now } : r));
          });
          ok += 1;
        } catch (error) {
          problem = reason(error);
          if (error instanceof ApiError && (error.kind === "network" || error.kind === "signed-out")) break;
        }
        const now = Math.floor(Date.now() / 1000);
        setRun((r) => (r ? { ...r, done: r.done + 1, moved: now } : r));
      }
    } finally {
      // What went wrong comes first: a distracted DM reads only the first line.
      const first = todo[0]!;
      // The count comes first when only some went through, so a distracted DM knows how many are left.
      const done =
        ok === 0 ? "" : ok < todo.length && todo.length > 1 ? w.someDone(ok, todo.length) : what === "undo" ? w.undone(first.spelling, first.keepName) : todo.length === 1 ? w.confirmed(first.spelling, first.keepName) : w.confirmedMany(ok);
      setSaid([done, problem].filter((x) => x !== "").join(" "));
      try {
        await read();
        onChanged?.();
      } finally {
        setRun(null);
        flight.current = false;
        summary.current?.focus();
      }
    }
  }

  if (!api.nameMatches) return null;
  if (load.kind === "ready" && load.matches.length === 0 && said === "" && run === null) return null;
  const matches = load.kind === "ready" ? load.matches : [];
  const busy = run !== null;
  return (
    <section class="panel stack" aria-labelledby="matches-h">
      <h2 id="matches-h">{w.heading}</h2>
      {load.kind === "loading" && (
        <p class="muted" role="status">
          {w.loading}
        </p>
      )}
      {load.kind === "error" && (
        <p class="warn" role="alert">
          {w.failed}
        </p>
      )}
      {matches.length > 0 && (
        <>
          <p>
            <strong>{w.countOf(matches.length)}</strong>
          </p>
          <p>{w.lead}</p>
          <ul class="stack">
            {matches.map((m) => (
              <li key={m.batch}>
                <p>{w.line(m.spelling, m.keepName)}</p>
                <div class="row">
                  <button type="button" class="button" aria-disabled={busy} onClick={() => !busy && void answer([m], "confirm")}>
                    {label(w.yes)}
                  </button>
                  <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void answer([m], "undo")}>
                    {label(w.undo)}
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {matches.length > 1 && (
            <div class="row">
              <p class="small muted">{w.checkFirst(matches.length)}</p>
              <button type="button" class="button" aria-disabled={busy} onClick={() => !busy && void answer(matches, "confirm")}>
                {label(w.all(matches.length))}
              </button>
            </div>
          )}
        </>
      )}
      {run && <WorkProgress stage={w.stage} startedAt={run.since} updatedAt={run.moved} serverNow={Math.floor(Date.now() / 1000)} state="running" done={run.done} total={run.total} unit={w.unit} />}
      <p class="small" role="status" tabIndex={-1} ref={summary}>
        {said}
      </p>
    </section>
  );
}
