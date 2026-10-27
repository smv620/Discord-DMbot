// A wait the API gives no progress for (#2565; owner rule 2026-10-23: every wait shows its stage, how long it has run and
// when it last moved). The page notes when the wait began and when what it can see last changed (`signature`: a
// count, a state, the names still waiting), and hands both to the shared wait panel, so "running for" and "last
// progress" are true to what the page saw and claim no more. Seconds, the page's own clock.
import { useEffect, useRef, useState } from "preact/hooks";

export interface ObservedWait {
  /** When the wait began (page clock, seconds), or null while nothing waits. */
  startedAt: number | null;
  /** When what the page can see last changed (page clock, seconds). */
  movedAt: number | null;
  /** True once nothing the page can see has changed for QUIET_SECONDS: the panel then says "no news for a while" (never a made-up percentage). */
  quiet: boolean;
}

export const QUIET_SECONDS = 60;

type Moments = Omit<ObservedWait, "quiet">;

export function useObservedWait(active: boolean, signature: string): ObservedWait {
  const [wait, setWait] = useState<Moments>({ startedAt: null, movedAt: null });
  const last = useRef(signature);
  useEffect(() => {
    const now = Math.floor(Date.now() / 1000);
    if (!active) {
      setWait((was) => (was.startedAt === null ? was : { startedAt: null, movedAt: null }));
    } else {
      setWait((was) => (was.startedAt === null ? { startedAt: now, movedAt: now } : was));
    }
  }, [active]);
  useEffect(() => {
    if (!active || last.current === signature) {
      last.current = signature;
      return;
    }
    last.current = signature;
    setWait((was) => (was.startedAt === null ? was : { ...was, movedAt: Math.floor(Date.now() / 1000) }));
  }, [active, signature]);
  // A timer, not a clock read at render: the page may not render again while nothing changes.
  const [quiet, setQuiet] = useState(false);
  useEffect(() => {
    setQuiet(false);
    if (wait.movedAt === null) return;
    const left = wait.movedAt + QUIET_SECONDS - Math.floor(Date.now() / 1000);
    if (left <= 0) {
      setQuiet(true);
      return;
    }
    const timer = setTimeout(() => setQuiet(true), left * 1000);
    return () => clearTimeout(timer);
  }, [wait.movedAt]);
  return { ...wait, quiet };
}
