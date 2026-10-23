/** @jsxImportSource preact */
// One small component for every wait on the website (#2565; owner rule 2026-10-23): the stage in plain words,
// how far along it is where known, how long it has been running (ticking while the page is open) and when
// it last moved forward. A stalled job says so and what to do; a failed one says what happened and what to
// do next. The page does the polling (bounded, backing off) and hands over what the API said; this only
// shows it, the same way on every page. The stage is read out when it changes (role="status"); the clocks
// are not in a live region, so they are not announced every second. Times are the server's seconds; the
// browser's clock is corrected by `serverNow` so a wrong clock on the computer does not show a wrong age.
import { useEffect, useRef, useState } from "preact/hooks";

import { workWords as words } from "../content/work-progress";

export interface WorkProgressProps {
  /** The stage in plain words (the page looks the API's code up with `stageWords`). */
  stage: string;
  /** How far along, when the API knows: with `unit`, "3 of 10 sessions". */
  done?: number | null;
  total?: number | null;
  unit?: string;
  /** When the work started and when it last moved forward, in the server's seconds. */
  startedAt?: number | null;
  updatedAt?: number | null;
  /** The server's clock when it answered (seconds), to correct this browser's clock. */
  serverNow?: number | null;
  state: "running" | "stalled" | "failed" | "done";
  /** Plain words on a failure, from the API. */
  reason?: string | null;
  /** What to do next, when the page knows better than the general words. */
  whatToDo?: string;
}

export default function WorkProgress({ stage, done, total, unit, startedAt, updatedAt, serverNow, state, reason, whatToDo }: WorkProgressProps) {
  // The server's clock now, kept as its gap from this browser's clock when the answer arrived.
  const gap = useRef(0);
  const lastServerNow = useRef<number | null | undefined>(undefined);
  const [, redraw] = useState(0);
  if (typeof serverNow === "number" && serverNow !== lastServerNow.current) {
    lastServerNow.current = serverNow;
    gap.current = serverNow - Date.now() / 1000;
  }
  const ticking = state === "running" || state === "stalled";
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => redraw((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [ticking]);

  const now = Date.now() / 1000 + gap.current;
  const hasCount = typeof done === "number" && typeof total === "number" && total > 0;
  const elapsed = typeof startedAt === "number" ? words.duration(now - startedAt) : null;
  const since = typeof updatedAt === "number" ? now - updatedAt : null;

  return (
    <div class="work-progress stack" data-state={state}>
      <p role={state === "failed" ? "alert" : "status"}>
        <strong>{state === "failed" ? words.failed : state === "done" ? words.done : stage}</strong>
        {hasCount && state !== "failed" && <span> {words.progress(done as number, total as number, unit)}</span>}
      </p>
      {hasCount && state === "running" && <progress max={total as number} value={Math.min(done as number, total as number)} aria-label={words.barLabel(stage)} />}
      {state === "failed" && (
        <>
          {reason && <p>{reason}</p>}
          <p class="small">{whatToDo ?? words.failedDo}</p>
        </>
      )}
      {state === "stalled" && (
        <>
          <p class="note" role="status">
            {words.stalled(since === null ? words.duration(0) : words.duration(since))}
          </p>
          <p class="small">{whatToDo ?? words.stalledDo}</p>
        </>
      )}
      {ticking && (
        <p class="small muted">
          {elapsed !== null && <span>{words.running(elapsed)} </span>}
          {since === null ? words.noChangeYet : since < 5 ? words.justNow : words.lastChange(words.duration(since))}
        </p>
      )}
    </div>
  );
}
