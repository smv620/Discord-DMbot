// Smooth counts for the shared wait panel (#3534, owner request 2026-11-06, ruled by Supervisor): the API sends a count
// ("40 of 100 lines") a few seconds apart, so between two real readings the panel shows an estimate that moves a few
// times a second, marked "about", and snaps to the real number at the next reading. The rules (Supervisor, #3534):
//   1. the rate comes from the last two real readings, nothing else;
//   2. the shown number never goes past the total, nor more than one reading interval ahead of the last reading;
//   3. when a reading is overdue it freezes (it cannot climb past the one-interval cap, so a late reading never
//      drags it on);
//   4. an estimated number is said to be "about" (the caller does that: `estimated` is true);
//   5. with reduced motion asked for, only real readings are shown.
// Counts only: nothing here knows a word of any document. It costs the server nothing.
import { useEffect, useRef, useState } from "preact/hooks";

/** How often the estimate is worked out while it can move (a little under 8 times a second). */
export const TICK_MS = 125;

const reducedMotion = (): boolean => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

interface Reading {
  done: number;
  at: number; // ms, this browser's clock when the reading arrived
}

/** The number to show now, given the last two readings (pure, so the rules are tested without a page). */
export function estimate(prev: Reading | null, last: Reading, now: number, total: number | null): number {
  if (prev === null) return last.done;
  const interval = last.at - prev.at;
  const rate = interval > 0 ? (last.done - prev.done) / interval : 0; // per ms; never negative
  if (rate <= 0) return last.done;
  const ahead = Math.min(Math.max(0, now - last.at), interval); // rules 2 and 3: at most one interval ahead
  const guess = Math.floor(last.done + rate * ahead);
  return total === null ? guess : Math.min(guess, total);
}

/** `done` as it should be shown, and whether it is an estimate. `reading` changes with every answer the API gives (its
 * server time), so a reading that finds the same count still counts as one (the estimate then stops). */
export function useSmoothCount(
  done: number | null | undefined,
  total: number | null | undefined,
  running: boolean,
  reading: unknown,
): { shown: number | null; estimated: boolean } {
  const readings = useRef<Reading[]>([]);
  const lastKey = useRef<string>("");
  const [, redraw] = useState(0);
  const counted = typeof done === "number";
  const key = `${done}|${String(reading)}`;
  if (counted && key !== lastKey.current) {
    lastKey.current = key;
    const kept = readings.current;
    // A count that goes back (a new stage) starts the readings again: no rate across a reset.
    if (kept.length > 0 && (done as number) < (kept[kept.length - 1] as Reading).done) kept.length = 0;
    kept.push({ done: done as number, at: Date.now() });
    if (kept.length > 2) kept.shift();
  }
  const moving = counted && running && !reducedMotion();
  useEffect(() => {
    if (!moving) return;
    const timer = setInterval(() => redraw((n) => n + 1), TICK_MS);
    return () => clearInterval(timer);
  }, [moving]);
  if (!counted) return { shown: null, estimated: false };
  const kept = readings.current;
  const last = kept[kept.length - 1] ?? { done: done as number, at: Date.now() };
  if (!moving) return { shown: done as number, estimated: false };
  const shown = estimate(kept.length > 1 ? (kept[0] as Reading) : null, last, Date.now(), typeof total === "number" && total > 0 ? total : null);
  return { shown, estimated: shown > (done as number) };
}
