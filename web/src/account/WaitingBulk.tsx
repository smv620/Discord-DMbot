/** @jsxImportSource preact */
// "✅ All true" and "🤫 All secret" above the claims DMbot heard at the table (#3645, parity with Discord's #3624). Each
// answers only the claims shown that are still waiting, one answer at a time, exactly as the claim's own button would:
// the same queue, the same checks, so a refused one is counted with its plain reason and the rest still go in. All secret
// leaves the states (which can't be secret) for the game master and says how many. One "Undo all N" takes the last press
// back, answer by answer, and is offered only when the answers say which change each made. DM only: the list it works on
// is the DM's alone. Counts and stages only; never a word of a claim.
import { useEffect, useRef, useState } from "preact/hooks";

import { bulkWords as w } from "../content/waiting-bulk-words";
import { waitingWords as ww } from "../content/web-waiting";
import { ApiError, type AccountApi, type WaitingClaim } from "./api";
import WorkProgress from "./WorkProgress";

const why = (error: unknown): string => (error instanceof ApiError ? ww.errors[error.kind] : undefined) ?? ww.errors.other ?? w.fallbackReason;

export default function WaitingBulk({
  api,
  campaignRef,
  claims,
  locked,
  onChanged,
  onRunning,
}: {
  api: AccountApi;
  campaignRef: string;
  /** The claims shown that are still waiting (the ordinary ones, not a question about which name). */
  claims: WaitingClaim[];
  /** True while another answer is on the way: the buttons wait for it. */
  locked: boolean;
  /** Reads the list again, and the graph. */
  onChanged: () => Promise<void>;
  /** Told when a press starts and ends, so the claims' own buttons wait too: one answer per claim, never two. */
  onRunning: (on: boolean) => void;
}) {
  const [run, setRun] = useState<{ done: number; total: number; since: number; moved: number } | null>(null);
  const [said, setSaid] = useState<string[]>([]);
  const [last, setLast] = useState<{ id: string; batch: number }[]>([]);
  const flight = useRef(false);
  const summary = useRef<HTMLParagraphElement>(null); // the buttons may go with the answered claims: focus goes to what was said
  const busy = run !== null || locked;
  const alive = useRef(true); // a press stops when the game master leaves: nothing more is answered for someone who has gone
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  // The parent hears whether a press is on, for the claims' own buttons.
  useEffect(() => {
    onRunning(run !== null);
  }, [run !== null]);

  async function press(secret: boolean): Promise<void> {
    if (flight.current || !api.answerWaiting) return;
    // All secret leaves a state alone (it can't be secret); the rest are answered one by one, in the order shown.
    const todo = claims.filter((c) => !secret || c.canSecret);
    const left = claims.length - todo.length;
    if (todo.length === 0) return;
    flight.current = true;
    setSaid([]);
    setLast([]);
    const since = Math.floor(Date.now() / 1000);
    setRun({ done: 0, total: todo.length, since, moved: since });
    let kept = 0;
    let shown = 0; // how many the bot showed to the players, and how many it kept hidden (#3974, #3986)
    let held = 0;
    let tried = 0;
    const refused = new Map<string, number>(); // the plain reason, and how many were refused for it
    const undoable: { id: string; batch: number }[] = [];
    try {
      for (const claim of todo) {
        if (!alive.current) break;
        tried += 1;
        try {
          const got = await api.answerWaiting(campaignRef, { kind: "claim", action: "confirm", id: claim.id, ...(secret ? { secret: true } : {}) });
          kept += 1;
          shown += got.shown ?? 0;
          held += got.held ?? 0;
          if (typeof got.batch === "number") undoable.push({ id: claim.id, batch: got.batch });
        } catch (error) {
          // A refusal is counted with its plain reason; a lost connection or sign-in stops the rest.
          const reason = why(error);
          refused.set(reason, (refused.get(reason) ?? 0) + 1);
          if (error instanceof ApiError && (error.kind === "network" || error.kind === "signed-out")) break;
        }
        const now = Math.floor(Date.now() / 1000);
        setRun((r) => (r ? { ...r, done: r.done + 1, moved: now } : r));
      }
    } finally {
      // What went wrong comes first: a distracted game master reads only the first line.
      setSaid(
        [
          ...[...refused].map(([reason, n]) => w.refused(n, reason)),
          tried < todo.length ? w.stoppedEarly(todo.length - tried) : "",
          kept > 0 ? (secret ? w.markedSecret(kept) : w.markedTrue(kept)) : "",
          shown > 0 ? w.shownMany(shown) : "",
          held > 0 ? w.heldMany(held) : "",
          secret && left > 0 ? w.skipped(left) : "",
          kept > 0 && undoable.length === 0 ? w.noUndo : "",
        ].filter((x) => x !== ""),
      );
      setLast(undoable);
      // Whatever happens to the re-read (a lost connection is likely here), the buttons must work again.
      try {
        await onChanged();
      } catch {
        // (the list shows what it showed: the summary above is what was done)
      } finally {
        setRun(null);
        flight.current = false;
        summary.current?.focus();
      }
    }
  }

  async function undoAll(): Promise<void> {
    if (flight.current || !api.answerWaiting || last.length === 0) return;
    flight.current = true;
    const todo = last;
    const since = Math.floor(Date.now() / 1000);
    setRun({ done: 0, total: todo.length, since, moved: since });
    setSaid([]);
    let undone = 0;
    let left = 0;
    try {
      let lost = false;
      for (const a of todo) {
        if (!alive.current || lost) {
          left += 1;
          continue;
        }
        try {
          await api.answerWaiting(campaignRef, { kind: "claim", action: "undo", id: a.id, batch: a.batch });
          undone += 1;
        } catch (error) {
          left += 1; // (something changed since: Discord says the same)
          lost = error instanceof ApiError && (error.kind === "network" || error.kind === "signed-out"); // (the rest are not tried)
        }
        const now = Math.floor(Date.now() / 1000);
        setRun((r) => (r ? { ...r, done: r.done + 1, moved: now } : r));
      }
    } finally {
      setSaid([left > 0 ? w.couldNotUndo(left) : "", undone > 0 ? w.undid(undone) : ""].filter((x) => x !== ""));
      setLast([]);
      // Whatever happens to the re-read, the buttons must work again.
      try {
        await onChanged();
      } catch {
        // (the list shows what it showed: the summary above is what was done)
      } finally {
        setRun(null);
        flight.current = false;
        summary.current?.focus();
      }
    }
  }

  const secretCount = claims.filter((c) => c.canSecret).length;
  const label = (b: { icon: string; text: string }) => (
    <>
      <span aria-hidden="true">{b.icon} </span>
      {b.text}
    </>
  );
  if (claims.length === 0 && last.length === 0 && said.length === 0 && run === null) return null;
  return (
    <div class="stack">
      <div class="row">
        {claims.length > 0 && (
          <>
            <button type="button" class="button" aria-disabled={busy} aria-describedby="bulk-hint" onClick={() => !busy && void press(false)}>
              {label(w.allTrue(claims.length))}
            </button>
            <button type="button" class="button secret" aria-disabled={busy} aria-describedby="bulk-hint bulk-hint-secret" onClick={() => !busy && void press(true)}>
              {label(w.allSecret(secretCount))}
            </button>
          </>
        )}
      </div>
      {last.length > 0 && (
        <div class="row">
          <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void undoAll()}>
            {label(w.undo(last.length))}
          </button>
        </div>
      )}
      {claims.length > 0 && (
        <>
          <p class="small muted" id="bulk-hint">{w.hint}</p>
          {claims.some((c) => !c.canSecret) && <p class="small muted" id="bulk-hint-secret">{w.hintSecret}</p>}
        </>
      )}
      {run && <WorkProgress stage={w.stage} startedAt={run.since} updatedAt={run.moved} serverNow={Math.floor(Date.now() / 1000)} state="running" done={run.done} total={run.total} unit={w.unit} />}
      <p class="small" role="status" tabIndex={-1} ref={summary}>
        {said.join(" ")}
      </p>
    </div>
  );
}
