/** @jsxImportSource preact */
// The Plot threads panel on the Names page (#3696, slice 1): the campaign's open plot threads, read only, for the DM. Its own file, fetched
// only for a DM while PLOT_THREADS_LIVE. Every word the answer carries (state, history, roles, the stalled line) is shown as core sent it;
// only the labels around them are worded here. A player gets nothing: the read refuses them, and the panel draws nothing.
import { useEffect, useRef, useState } from "preact/hooks";

import { PLOT_THREADS_LIVE } from "../content/plot-threads-switch";
import { plotThreadsWords as w, plotThreadStates } from "../content/plot-threads-words";
import { ApiError, type AccountApi, type PlotThread, type PlotThreadState, type PlotThreads } from "./api";
import SavingWait from "./SavingWait";

// Names listed before "+N more" in Came from and Led to, as on Discord's card.
const CUT = 5;
// A read that has run this long says so, instead of showing the same words forever.
const SLOW_MS = 15000;

export default function PlotThreadsPanel({ api, refId, onSignedOut }: { api: AccountApi; refId: string; onSignedOut?: () => void }) {
  const [list, setList] = useState<PlotThreads | null>(null);
  const [problem, setProblem] = useState<"failed" | "hidden" | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [slow, setSlow] = useState(false);
  // The thread whose state buttons are open, the one press with the bot (one at a time), and what the last press said.
  const [changing, setChanging] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const statusRef = useRef<HTMLParagraphElement | null>(null);

  // Each read takes the next number; an answer that is not the latest read's is dropped, so an old campaign or an old try cannot overwrite.
  const latest = useRef(0);

  /** Reads the threads for this campaign: the first look, and each "Try again". */
  function read(): void {
    if (!PLOT_THREADS_LIVE || !api.plotThreads) return;
    const mine = ++latest.current;
    // A new read drops what was there: a change of campaign, or a retry, shows the loading line again.
    setList(null);
    setProblem(null);
    setSlow(false);
    const slowTimer = setTimeout(() => {
      if (mine === latest.current) setSlow(true);
    }, SLOW_MS);
    api.plotThreads(refId).then(
      (got) => {
        clearTimeout(slowTimer);
        if (mine !== latest.current) return;
        setList(got);
        setProblem(null);
      },
      (error: unknown) => {
        clearTimeout(slowTimer);
        if (mine !== latest.current) return;
        if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
        // A player, or another campaign's DM, is refused: nothing is drawn, not an error.
        if (error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found")) setProblem("hidden");
        else setProblem("failed");
      },
    );
  }

  // Focus moves to the line that says what happened, so a screen reader reads it at once.
  useEffect(() => {
    if (said) statusRef.current?.focus();
  }, [said]);

  /** Reads the list again after a change, without blanking it: a failed read keeps what is shown. */
  async function refresh(): Promise<void> {
    if (!api.plotThreads) return;
    const mine = ++latest.current;
    try {
      const got = await api.plotThreads(refId);
      if (mine === latest.current) setList(got);
    } catch {
      // The list shown stays; the next look (or Try again) reads it.
    }
  }

  /** One state change, or "check" (still going), for one thread. A success closes the buttons; a failure leaves them open to try again. */
  async function press(t: PlotThread, state: PlotThreadState | "check"): Promise<void> {
    if (busy || !api.changePlotThreadState) return;
    setBusy(true);
    setSaid(null);
    try {
      await api.changePlotThreadState(refId, { thread: t.id, state });
      setChanging(null);
      setSaid({ ok: true, text: state === "check" ? w.checkedDone : w.markedDone(w.stateWord(t.kind === "promise", state)) });
    } catch (error) {
      setSaid({ ok: false, text: error instanceof ApiError && error.kind === "gone" ? w.gone : w.couldNot });
    } finally {
      await refresh();
      setBusy(false);
    }
  }

  useEffect(() => {
    read();
    // A campaign change (or unmount) makes the read in flight stale, so its answer is dropped.
    return () => {
      latest.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, refId]);

  if (!PLOT_THREADS_LIVE || !api.plotThreads || problem === "hidden") return null;
  if (problem === "failed") {
    return (
      <section class="stack" aria-label={w.heading}>
        <h2 class="small">{w.heading}</h2>
        <p role="alert">{w.failed}</p>
        <button type="button" class="button secondary" onClick={() => read()}>
          {w.tryAgain}
        </button>
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
      {said && (
        <p ref={statusRef} tabIndex={-1} role={said.ok ? "status" : "alert"} class={said.ok ? "ok" : "warn"}>
          {said.text}
        </p>
      )}
      {stalledText && <p role="status">{stalledText}</p>}
      {list.threads.length === 0 ? (
        <p class="muted">{w.none}</p>
      ) : (
        <div role="region" aria-label={w.listLabel} class="stack">
          <ul class="stack">
            {list.threads.map((t) => (
              <li key={t.id}>
                <Thread
                  thread={t}
                  isOpen={!!open[t.id]}
                  onToggle={() => setOpen((prev) => ({ ...prev, [t.id]: !prev[t.id] }))}
                  changing={changing === t.id}
                  canChange={!!api.changePlotThreadState}
                  busy={busy}
                  onChange={() => setChanging(changing === t.id ? null : t.id)}
                  onPress={(state) => void press(t, state)}
                />
              </li>
            ))}
          </ul>
        </div>
      )}
      {list.total > list.threads.length && <p class="small muted">{w.showing(list.total)}</p>}
    </section>
  );
}

/** One thread: its name, state, stalled badge, who is involved, the events that moved it, the history behind a toggle, and Change state. */
function Thread({
  thread: t,
  isOpen,
  onToggle,
  changing,
  canChange,
  busy,
  onChange,
  onPress,
}: {
  thread: PlotThread;
  isOpen: boolean;
  onToggle: () => void;
  changing: boolean;
  canChange: boolean;
  busy: boolean;
  onChange: () => void;
  onPress: (state: PlotThreadState | "check") => void;
}) {
  const historyId = `plot-history-${t.id}`;
  const changeId = `plot-change-${t.id}`;
  const promise = t.kind === "promise";
  // "Checked, still going" is offered only while the thread is live: hinted, open or happening now.
  const live = t.state === "hinted" || t.state === "open" || t.state === "active";
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
      {canChange && (
        <div class="stack">
          <button type="button" class="button secondary" aria-expanded={changing} aria-controls={changeId} disabled={busy} onClick={onChange}>
            {changing ? w.closeChange : w.changeState}
          </button>
          {changing && (
            <div id={changeId} class="stack" role="group" aria-label={`${w.changeState}: ${t.name}`}>
              <div class="row">
                {plotThreadStates.map((s) => (
                  <button
                    key={s}
                    type="button"
                    class={t.state === s ? "button" : "button secondary"}
                    disabled={busy}
                    onClick={() => onPress(s)}
                  >
                    {w.stateWord(promise, s)}
                  </button>
                ))}
              </div>
              {live && (
                <div class="row">
                  <button type="button" class="button secondary" disabled={busy} onClick={() => onPress("check")}>
                    {w.checked}
                  </button>
                </div>
              )}
              <SavingWait active={busy} stage={w.working} />
            </div>
          )}
        </div>
      )}
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
