/** @jsxImportSource preact */
// The Plot threads panel on the Names page (#3696, slice 1): the campaign's open plot threads, read only, for the DM. Its own file, fetched
// only for a DM while PLOT_THREADS_LIVE. Every word the answer carries (state, history, roles, the stalled line) is shown as core sent it;
// only the labels around them are worded here. A player gets nothing: the read refuses them, and the panel draws nothing.
import { useEffect, useState } from "preact/hooks";

import { PLOT_THREADS_LIVE } from "../content/plot-threads-switch";
import { plotThreadsWords as w } from "../content/plot-threads-words";
import { ApiError, type AccountApi, type PlotThread, type PlotThreads } from "./api";

// Names listed before "+N more" in Came from and Led to, as on Discord's card.
const CUT = 5;
// A read that has run this long says so, instead of showing the same words forever.
const SLOW_MS = 15000;

export default function PlotThreadsPanel({ api, refId, onSignedOut }: { api: AccountApi; refId: string; onSignedOut?: () => void }) {
  const [list, setList] = useState<PlotThreads | null>(null);
  const [problem, setProblem] = useState<"failed" | "hidden" | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    if (!PLOT_THREADS_LIVE || !api.plotThreads) return;
    // A change of campaign drops the old campaign's threads and message until the new answer comes.
    setList(null);
    setProblem(null);
    setSlow(false);
    const slowTimer = setTimeout(() => setSlow(true), SLOW_MS);
    let stale = false;
    api.plotThreads(refId).then(
      (got) => {
        if (stale) return;
        clearTimeout(slowTimer);
        setList(got);
        setProblem(null);
      },
      (error: unknown) => {
        if (stale) return;
        clearTimeout(slowTimer);
        if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
        // A player, or another campaign's DM, is refused: nothing is drawn, not an error.
        if (error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found")) setProblem("hidden");
        else setProblem("failed");
      },
    );
    return () => {
      stale = true;
      clearTimeout(slowTimer);
    };
  }, [api, refId]);

  if (!PLOT_THREADS_LIVE || !api.plotThreads || problem === "hidden") return null;
  if (problem === "failed") {
    return (
      <section class="stack" aria-label={w.heading}>
        <h2 class="small">{w.heading}</h2>
        <p role="alert">{w.failed}</p>
      </section>
    );
  }
  if (!list) {
    return (
      <section class="stack" aria-label={w.heading}>
        <h2 class="small">{w.heading}</h2>
        <p class="muted" role="status">{w.loading}</p>
        {slow && <p class="small">{w.slow}</p>}
      </section>
    );
  }

  const stalledText =
    list.stalledLine ?? (list.stalledCount > 0 ? w.stalledLine(list.stalledCount, list.after) : null);

  return (
    <section class="stack" aria-label={w.heading}>
      <h2 class="small">{w.heading}</h2>
      <p class="small">{w.intro}</p>
      {stalledText && <p role="status">{stalledText}</p>}
      {list.threads.length === 0 ? (
        <p class="muted">{w.none}</p>
      ) : (
        <div role="region" aria-label={w.listLabel} class="stack">
          <ul class="stack">
            {list.threads.map((t) => (
              <li key={t.id}>
                <Thread thread={t} isOpen={!!open[t.id]} onToggle={() => setOpen((prev) => ({ ...prev, [t.id]: !prev[t.id] }))} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {list.total > list.threads.length && <p class="small muted">{w.showing(list.total)}</p>}
    </section>
  );
}

/** One thread: its name, state, stalled badge, who is involved, the events that moved it, and the history behind a toggle. */
function Thread({ thread: t, isOpen, onToggle }: { thread: PlotThread; isOpen: boolean; onToggle: () => void }) {
  const historyId = `plot-history-${t.id}`;
  const state = t.state === null || t.stateWords === "" ? w.noState : t.stateWords;
  return (
    <article class="stack">
      <p>
        <strong>{t.name}</strong>
        {t.kind !== "thread" && <span class="small muted"> · {t.kind}</span>}
      </p>
      <p class="small">
        {state}
        {t.stalled && (
          <span class="small muted">
            {" "}
            · {w.stalled}, {w.quiet(t.quiet)}
          </span>
        )}
      </p>
      {t.involved.length > 0 && (
        <p class="small">
          {w.involved} {t.involved.map((p) => w.person(p.name, p.role)).join(", ")}
        </p>
      )}
      {t.movers.length > 0 && (
        <p class="small">
          {w.movedBy} {t.movers.map((m) => (m.endsIt ? w.endedIt(m.name) : m.name)).join(", ")}
        </p>
      )}
      {t.cameFrom.length > 0 && <CutLine label={w.cameFrom} names={t.cameFrom.map((n) => n.name)} />}
      {t.ledTo.length > 0 && <CutLine label={w.ledTo} names={t.ledTo.map((n) => n.name)} />}
      {t.promise && t.promise.by.length > 0 && <p class="small">{w.madeBy} {t.promise.by.join(", ")}</p>}
      {t.promise && t.promise.to.length > 0 && <p class="small">{w.madeTo} {t.promise.to.join(", ")}</p>}
      {t.promise && t.promise.due !== "" && <p class="small">{w.due} {t.promise.due}</p>}
      {t.history.length > 0 && (
        <div>
          <button type="button" class="button secondary" aria-expanded={isOpen} aria-controls={historyId} onClick={onToggle}>
            {isOpen ? w.hideHistory : w.seeHistory}
          </button>
          <ul id={historyId} class="small" hidden={!isOpen}>
            {t.history.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

/** A label and its names, at most CUT of them, then "+N more" (Came from, Led to). */
function CutLine({ label, names }: { label: string; names: string[] }) {
  const shown = names.slice(0, CUT);
  const rest = names.length - shown.length;
  return (
    <p class="small">
      {label} {shown.join(", ")}
      {rest > 0 && <> {w.more(rest)}</>}
    </p>
  );
}
