/** @jsxImportSource preact */
// One small component for every wait on the website (#2565; owner rule 2026-10-23): the stage in plain words,
// how far along it is where known, how long it has been running (ticking while the page is open) and when
// it last moved forward. A stalled job says so and what to do; a failed one says what happened and what to
// do next. The page does the polling (bounded, backing off) and hands over what the API said; this only
// shows it, the same way on every page. The stage is read out when it changes (role="status"); the clocks
// are not in a live region, so they are not announced every second. Times are the server's seconds; the
// browser's clock is corrected by `serverNow` so a wrong clock on the computer does not show a wrong age.
import { Fragment } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { workWords as words } from "../content/work-progress";
import { useSmoothCount } from "./useSmoothCount";

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
  /** The page has stopped looking (the job is gone, or a look failed): no clock runs and none is shown, so a
   * "Running for 14 min" never sits beside a note that says the job is gone. */
  frozen?: boolean | undefined;
  /** Plain words on a failure, from the API. */
  reason?: string | null | undefined;
  /** The page has no sign of progress to show (a move, a download), so the "last moved" line is left out
   * rather than reading as a stuck job. Only the time running is shown. */
  progressUnknown?: boolean | undefined;
  /** Running counts (words processed, calls made, ...), each moving smoothly between readings (#3534). */
  counts?: Record<string, number> | null | undefined;
  /** What to do next, when the page knows better than the general words. */
  whatToDo?: string | undefined;
}

const COUNT_ORDER = ["words", "calls", "checks", "names"];
const visibleCounts = (counts: Record<string, number> | null | undefined): string[] =>
  counts ? COUNT_ORDER.filter((c) => typeof counts[c] === "number" && (counts[c] as number) > 0) : [];

/** One running count: smoothed between readings like the main count, and "about" only while it is an estimate. */
function CountItem({ code, value, running, reading }: { code: string; value: number; running: boolean; reading: unknown }) {
  const smooth = useSmoothCount(value, null, running, reading);
  const phrase = (words.countPhrase[code] as (n: number) => string)(smooth.shown ?? value);
  return <span>{smooth.estimated ? words.aboutCount(phrase) : phrase}</span>;
}

/** The counts line on its own (a session row uses it without a whole panel): only the counts the page has words for, in a fixed order, once something has been handled. */
export function RunningCounts({ counts, running, reading }: { counts: Record<string, number> | null | undefined; running: boolean; reading: unknown }) {
  const shown = visibleCounts(counts);
  if (!counts || shown.length === 0) return null;
  return (
    <span class="small muted">
      {shown.map((code, i) => (
        <Fragment key={code}>
          {i > 0 && " · "}
          <CountItem code={code} value={counts[code] as number} running={running} reading={reading} />
        </Fragment>
      ))}
    </span>
  );
}

export default function WorkProgress({ stage, done, total, unit, startedAt, updatedAt, serverNow, state, waiting, frozen, progressUnknown, reason, whatToDo, counts }: WorkProgressProps) {
  // The server's clock now, kept as its gap from this browser's clock when the answer arrived.
  const gap = useRef(0);
  const lastServerNow = useRef<number | null | undefined>(undefined);
  const [, redraw] = useState(0);
  if (typeof serverNow === "number" && serverNow !== lastServerNow.current) {
    lastServerNow.current = serverNow;
    gap.current = serverNow - Date.now() / 1000;
  }
  const ticking = (state === "running" || state === "stalled") && !waiting && !frozen;
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => redraw((n) => n + 1), 5000); // (the times change in 5 s steps, then minutes)
    return () => clearInterval(timer);
  }, [ticking]);

  const now = Date.now() / 1000 + gap.current;
  const hasCount = typeof done === "number" && typeof total === "number" && total > 0;
  // Between two real readings the count moves a few times a second, as "about" (#3534); reduced motion shows the real one.
  const smooth = useSmoothCount(done, total, state === "running" && !waiting && !frozen, serverNow);
  const elapsed = typeof startedAt === "number" ? words.duration(now - startedAt) : null;
  const since = typeof updatedAt === "number" ? now - updatedAt : null;

  return (
    <div class="work-progress stack" data-state={state}>
      {/* Only the stage is read out when it changes; the count is beside it, not in the live region, so a
          screen reader does not repeat the whole line at every step (the bar carries the count). */}
      <p>
        {state === "failed" ? (
          <strong role="alert">{words.failed}</strong>
        ) : (
          <strong role="status">{state === "done" ? words.done : stage}</strong>
        )}
        {hasCount && state !== "failed" && !waiting && (
          <span> {smooth.estimated ? words.progressAbout(smooth.shown as number, total as number, unit) : words.progress(done as number, total as number, unit)}</span>
        )}
      </p>
      {state !== "failed" && visibleCounts(counts).length > 0 && <p class="small muted"><RunningCounts counts={counts} running={state === "running" && !waiting && !frozen} reading={serverNow} /></p>}
      {hasCount && (state === "running" || state === "stalled") && !waiting && <progress max={total as number} value={Math.min((smooth.shown ?? done) as number, total as number)} aria-label={words.barLabel(stage)} />}
      {state === "failed" && (
        <>
          {reason && <p>{reason}</p>}
          <p class="small">{whatToDo ?? words.failedDo}</p>
        </>
      )}
      {state === "stalled" && (
        <>
          <p class="note">
            <span role="status">{words.stalled}</span>
            {/* (the length of the quiet is outside the live region: it changes every minute) */}
            {since !== null && !frozen && <span> {words.quietFor(words.duration(since))}</span>}
          </p>
          <p class="small">{whatToDo ?? words.stalledDo}</p>
        </>
      )}
      {frozen && state !== "done" && state !== "failed" && <p class="small muted">{words.stoppedLooking}</p>}
      {ticking && (
        <p class="small muted">
          {elapsed !== null && <span>{words.running(elapsed)} </span>}
          {/* A stall already says how long it has been quiet. */}
          {state === "stalled" || progressUnknown ? null : since === null ? words.noChangeYet : since < 5 ? words.justNow : words.lastChange(words.duration(since))}
        </p>
      )}
    </div>
  );
}
