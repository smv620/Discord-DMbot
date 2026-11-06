/** @jsxImportSource preact */
// "✅ All true" and "🤫 All secret" above the claims DMbot heard at the table (#3645, parity with Discord's #3624). Each
// answers only the claims shown that are still waiting, one answer at a time, exactly as the claim's own button would:
// the same queue, the same checks, so a refused one is counted with its plain reason and the rest still go in. All secret
// leaves the states (which can't be secret) for the game master and says how many. One "Undo all N" takes the last press
// back, answer by answer, and is offered only when the answers say which change each made. DM only: the list it works on
// is the DM's alone. Counts and stages only; never a word of a claim.
import { useRef, useState } from "preact/hooks";

import { bulkWords as w } from "../content/waiting-bulk-words";
import { waitingWords as ww } from "../content/web-waiting";
import { ApiError, type AccountApi, type WaitingClaim } from "./api";
import WorkProgress from "./WorkProgress";

const why = (error: unknown): string => (error instanceof ApiError ? ww.errors[error.kind] ?? ww.errors.other! : ww.errors.other!);

export default function WaitingBulk({
  api,
  campaignRef,
  claims,
  locked,
  onChanged,
}: {
  api: AccountApi;
  campaignRef: string;
  /** The claims shown that are still waiting (the ordinary ones, not a question about which name). */
  claims: WaitingClaim[];
  /** True while another answer is on the way: the buttons wait for it. */
  locked: boolean;
  /** Reads the list again, and the graph. */
  onChanged: () => Promise<void>;
}) {
  const [run, setRun] = useState<{ done: number; total: number; since: number; moved: number } | null>(null);
  const [said, setSaid] = useState<string[]>([]);
  const [last, setLast] = useState<{ id: string; batch: number }[]>([]);
  const flight = useRef(false);
  const busy = run !== null || locked;

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
    const refused: string[] = [];
    const undoable: { id: string; batch: number }[] = [];
    try {
      for (const claim of todo) {
        try {
          const got = await api.answerWaiting(campaignRef, { kind: "claim", action: "confirm", id: claim.id, ...(secret ? { secret: true } : {}) });
          kept += 1;
          if (typeof got.batch === "number") undoable.push({ id: claim.id, batch: got.batch });
        } catch (error) {
          // A refusal is counted with its plain reason; a lost connection or sign-in stops the rest.
          refused.push(why(error));
          if (error instanceof ApiError && (error.kind === "network" || error.kind === "signed-out")) break;
        }
        const now = Math.floor(Date.now() / 1000);
        setRun((r) => (r ? { ...r, done: r.done + 1, moved: now } : r));
      }
    } finally {
      const reasons = [...new Set(refused)];
      setSaid([
        kept > 0 ? (secret ? w.keptSecret(kept) : w.keptTrue(kept)) : "",
        secret && left > 0 ? w.skipped(left) : "",
        refused.length > 0 ? w.refused(refused.length, reasons.join(" ")) : "",
      ].filter((x) => x !== ""));
      setLast(undoable);
      await onChanged();
      setRun(null);
      flight.current = false;
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
      for (const a of todo) {
        try {
          await api.answerWaiting(campaignRef, { kind: "claim", action: "undo", id: a.id, batch: a.batch });
          undone += 1;
        } catch {
          left += 1; // (something changed since: Discord says the same)
        }
        const now = Math.floor(Date.now() / 1000);
        setRun((r) => (r ? { ...r, done: r.done + 1, moved: now } : r));
      }
    } finally {
      setSaid([undone > 0 ? w.undid(undone) : "", left > 0 ? w.couldNotUndo(left) : ""].filter((x) => x !== ""));
      setLast([]);
      await onChanged();
      setRun(null);
      flight.current = false;
    }
  }

  if (claims.length === 0 && last.length === 0 && said.length === 0 && run === null) return null;
  return (
    <div class="stack">
      <div class="row">
        {claims.length > 0 && (
          <>
            <button type="button" class="button" aria-disabled={busy} aria-describedby="bulk-hint-true" onClick={() => !busy && void press(false)}>
              {w.allTrue}
            </button>
            <button type="button" class="button secondary" aria-disabled={busy} aria-describedby="bulk-hint-secret" onClick={() => !busy && void press(true)}>
              {w.allSecret}
            </button>
          </>
        )}
        {last.length > 0 && (
          <button type="button" class="button secondary" aria-disabled={busy} onClick={() => !busy && void undoAll()}>
            {w.undo(last.length)}
          </button>
        )}
      </div>
      {claims.length > 0 && (
        <>
          <p class="small muted" id="bulk-hint-true">{w.hintTrue}</p>
          <p class="small muted" id="bulk-hint-secret">{w.hintSecret}</p>
        </>
      )}
      {run && <WorkProgress stage={w.stage} startedAt={run.since} updatedAt={run.moved} serverNow={Math.floor(Date.now() / 1000)} state="running" done={run.done} total={run.total} unit={w.unit} />}
      {said.length > 0 && (
        <p class="small" role="status">
          {said.join(" ")}
        </p>
      )}
    </div>
  );
}
