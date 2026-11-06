/** @jsxImportSource preact */
// A panel the recap page fetches after the page itself, only when it is needed (#2690): the DM-only panels
// (Names to check, Plot check, Names fixed, Who was in each scene, After the session) and the heavier parts
// (the recaps maker, fix-a-word, the names list, the player) are separate files, so the first download stays
// light. The caller tests the panel's switch (and, for the DM-only panels, the viewer) first, so a panel that
// is off, or not for this viewer, never asks for its code. `load` must stay the same for one instance (use a
// `key` to show another panel). While the code is fetched it says so; after 5 seconds it says it is slow;
// after 20 it offers Try again; a failed fetch says what to do, with Try again and Reload the page (a
// browser may remember a failed import until the page is loaded again).
import type { ComponentType } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { lazyWords as w } from "../content/lazy-panel-words";

const SLOW_MS = 5000;
const STUCK_MS = 20_000; // after this the wait offers Try again: a hung fetch must not leave the DM with no way out

export default function LazyPanel<P extends object>({
  load,
  what,
  props,
}: {
  /** `() => import("./Panel")`: written at the call site so the build makes the panel its own file. */
  load: () => Promise<{ default: ComponentType<P> }>;
  /** The panel in plain words, for "Loading … ". */
  what: string;
  props: P;
}) {
  // (stored in an object because a component is itself a function, which a state setter would call)
  const [panel, setPanel] = useState<{ Panel: ComponentType<P> } | null>(null);
  const [failed, setFailed] = useState(false);
  const [slow, setSlow] = useState(false);
  const [stuck, setStuck] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const status = useRef<HTMLParagraphElement>(null); // focus goes here after Try again, so the keyboard keeps its place

  useEffect(() => {
    let live = true;
    setFailed(false);
    setSlow(false);
    setStuck(false);
    const timer = setTimeout(() => live && setSlow(true), SLOW_MS);
    const long = setTimeout(() => live && setStuck(true), STUCK_MS);
    if (attempt > 0) setTimeout(() => status.current?.focus(), 0);
    load().then(
      (m) => {
        clearTimeout(timer);
        clearTimeout(long);
        if (live) setPanel({ Panel: m.default });
      },
      () => {
        clearTimeout(timer);
        clearTimeout(long);
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
      clearTimeout(timer);
      clearTimeout(long);
    };
    // The panel is fetched once per attempt, not again each time the page re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  if (panel) return <panel.Panel {...props} />;
  if (failed) {
    return (
      <div class="stack">
        <p class="error" role="alert">
          {w.failed(what)}
        </p>
        <div class="row">
          <button type="button" class="button secondary" onClick={() => setAttempt((n) => n + 1)}>
            {w.retry}
          </button>
          <button type="button" class="button secondary" onClick={() => window.location.reload()}>
            {w.reload}
          </button>
        </div>
      </div>
    );
  }
  return (
    <div class="stack">
      <p class="small muted" role="status" tabIndex={-1} ref={status}>
        {stuck ? w.stuck(what) : slow ? w.slow(what) : w.loading(what)}
      </p>
      {stuck && (
        <button type="button" class="button secondary" onClick={() => setAttempt((n) => n + 1)}>
          {w.retry}
        </button>
      )}
    </div>
  );
}
