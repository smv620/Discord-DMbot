// The half-second counts read of a running upload or import (#3534 part 3; core #5286, behind WAIT_COUNTS_LIVE). While a job runs
// AND this tab is visible it asks for the job's counts again as soon as the last answer is in and half a second has passed (never
// two asks at once), and it stops at once when the tab is hidden, the job is over or a read fails (the page's own slower look then
// carries on, so nothing is lost). Counts and times only. The reading is only ever used to show the numbers sooner: the stage, the
// state and every word on the page still come from the slower look.
import { useEffect, useState } from "preact/hooks";

import { WAIT_COUNTS_LIVE } from "../content/wait-counts-switch";
import type { AccountApi, WaitCountsImport, WaitCountsLore } from "./api";

export const COUNTS_EVERY_MS = 500;
/** Reads that fail one after another before the page stops asking (the slower look carries on alone). */
export const MAX_FAILURES = 3;

type Reading<K extends "lore" | "import"> = K extends "lore" ? WaitCountsLore : WaitCountsImport;

/** The newest counts reading while `active`, else null. `active` is false for a finished job, so a finished job is never asked. */
export function useWaitCounts<K extends "lore" | "import">(api: AccountApi, ref: string | null, kind: K, id: string | null, active: boolean): Reading<K> | null {
  const [reading, setReading] = useState<Reading<K> | null>(null);
  const on = WAIT_COUNTS_LIVE && active && Boolean(ref && id && api.waitCounts);
  useEffect(() => {
    setReading(null); // (never the last job's numbers under a new job)
    if (!on || !ref || !id || !api.waitCounts) return;
    let live = true;
    let inflight = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const visible = (): boolean => typeof document === "undefined" || document.visibilityState !== "hidden";
    const stop = (): void => {
      if (timer) clearTimeout(timer);
      timer = undefined;
    };
    const look = async (): Promise<void> => {
      timer = undefined;
      if (!live || inflight || !visible()) return; // (one ask at a time, ever)
      inflight = true;
      let carryOn = true;
      try {
        const got = (await api.waitCounts!(ref, kind, id)) as Reading<K>;
        if (!live) return;
        failures = 0;
        setReading(got);
        // (an import's end is seen by the page's own slower look, which turns `active` off: a few more asks at most)
        if (kind === "lore" && ((got as WaitCountsLore).state === "done" || (got as WaitCountsLore).state === "failed")) carryOn = false;
      } catch {
        failures += 1;
        carryOn = failures < MAX_FAILURES; // (a blip is tried again; a job that is gone or refused stops for good)
      } finally {
        inflight = false;
      }
      if (live && carryOn && visible() && timer === undefined) timer = setTimeout(() => void look(), COUNTS_EVERY_MS);
    };
    const onVisibility = (): void => {
      if (!visible()) stop();
      else if (live && !inflight && timer === undefined) void look();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void look();
    return () => {
      live = false;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [api, ref, kind, id, on]);
  return on ? reading : null;
}
