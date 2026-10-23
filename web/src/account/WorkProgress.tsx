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
  done?: number | null | undefined;
  total?: number | null | undefined;
  unit?: string | undefined;
  /** When the work started and when it last moved forward, in the server's seconds. */
  startedAt?: number | null | undefined;
  updatedAt?: number | null | undefined;
  /** The server's clock when it answered (seconds), to correct this browser's clock. */
  serverNow?: number | null | undefined;
  state: "running" | "stalled" | "failed" | "done";
  /** Waiting for the person (not for DMbot): the stage says so, and no clock or bar runs. */
  waiting?: boolean | undefined;
  /** Plain words on a failure, from the API. */
  reason?: string | null | undefined;
  /** What to do next, when the page knows better than the general words. */
  whatToDo?: string | undefined;
}

export default function WorkProgress({ stage, done, total, unit, startedAt, updatedAt, serverNow, state, waiting, reason, whatToDo }: WorkProgressProps) {
  // The server's clock now, kept as its gap from this browser's clock when the answer arrived.
  const gap = useRef(0);
  const lastServerNow = useRef<number | null | undefined>(undefined);
  const [, redraw] = useState(0);
  if (typeof serverNow === "number" && serverNow !== lastServerNow.current) {
    lastServerNow.current = serverNow;
    gap.current = serverNow - Date.now() / 1000;
  }
  const ticking = (state === "running" || state === "stalled") && !waiting;
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => redraw((n) => n + 1), 5000); // (the times change in 5 s steps, then minutes)
    return () => clearInterval(timer);
  }, [ticking]);

  const now = Date.now() / 1000 + gap.current;
  const hasCount = typeof done === "number" && typeof total === "number" && total > 0;
  const elapsed = typeof startedAt === "number" ? words.duration(now - startedAt) : null;
  const since = typeof updatedAt === "number" ? now - updatedAt : null;

  return (
    <div class="work-progress stack" data-state={state}>
      {/* Only the stage is read out when it changes; the count is beside it, not in the live region, so a
          screen reader does not repeat the whole line at every step (the bar carries the count). */}
      <p>
        <strong role={state === "failed" ? "alert" : "status"}>{state === "failed" ? words.failed : state === "done" ? words.done : stage}</strong>
        {hasCount && state !== "failed" && !waiting && <span> {words.progress(done as number, total as number, unit)}</span>}
      </p>
      {hasCount && (state === "running" || state === "stalled") && !waiting && <progress max={total as number} value={Math.min(done as number, total as number)} aria-label={words.barLabel(stage)} />}
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
          {/* A stall already says how long it has been quiet. */}
          {state === "stalled" ? null : since === null ? words.noChangeYet : since < 5 ? words.justNow : words.lastChange(words.duration(since))}
        </p>
      )}
    </div>
  );
}
