/** @jsxImportSource preact */
// A panel the recap page fetches only when it is needed (#2690): the DM-only panels (Names to check, Plot
// check, Names fixed, Who was in each scene, After the session) are not in the page's first download, which
// keeps the page light for everyone else. The caller tests the panel's switch and the viewer first, so a
// player's page never even asks for the code. While the code is fetched it says so, after 5 seconds it says
// it is slow, and if the fetch fails it says what to do and offers to try again.
import type { ComponentType } from "preact";
import { useEffect, useState } from "preact/hooks";

import { lazyWords as w } from "../content/lazy-panel-words";

const SLOW_MS = 5000;

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
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setFailed(false);
    setSlow(false);
    const timer = setTimeout(() => live && setSlow(true), SLOW_MS);
    load().then(
      (m) => {
        if (live) setPanel({ Panel: m.default });
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
      clearTimeout(timer);
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
        <button type="button" class="button secondary" onClick={() => setAttempt((n) => n + 1)}>
          {w.retry}
        </button>
      </div>
    );
  }
  return (
    <p class="small muted" role="status" aria-live="polite">
      {slow ? w.slow(what) : w.loading(what)}
    </p>
  );
}
