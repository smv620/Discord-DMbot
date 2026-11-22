/** @jsxImportSource preact */
// The Plot threads panel on the Names page (#3696, slice 1): the campaign's open plot threads, read only, for the DM. Its own file, fetched
// only for a DM while PLOT_THREADS_LIVE. Every word the answer carries (state, history, roles, the stalled line) is shown as core sent it;
// only the labels around them are worded here. A player gets nothing: the read refuses them, and the panel draws nothing.
import { useEffect, useRef, useState } from "preact/hooks";

import { PLOT_THREADS_LIVE } from "../content/plot-threads-switch";
import { plotThreadsWords as w, plotThreadStates } from "../content/plot-threads-words";
import { ApiError, type AccountApi, type CampaignName, type PlotThread, type PlotThreadState, type PlotThreads } from "./api";

// The kinds a promise's "Who promised?" and "Promised to" name can be (#3696, slice 2c): a character or a faction, and for "to" a place too.
const BY_KINDS = ["character", "faction"];
const TO_KINDS = ["character", "faction", "place"];
// What a "Led to…" can be: an event, or a thread of any of its kinds (#3696, slice 2d).
const LED_KINDS = ["event", "thread", "quest", "mystery", "hook", "goal", "promise"];
// The four kinds a new story thread can be, as Discord's card offers them (a plain thread is not one of them).
const NEW_KINDS = ["quest", "mystery", "hook", "goal"] as const;
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
  // The one form open at a time (#3696, slices 2b and 2c): "new", "promise", or a thread's key: add:<id>, due:<id>, by:<id>, to:<id>.
  const [form, setForm] = useState<string | null>(null);
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

  /** Adds one name to a thread with its part; a success closes the form, a failure leaves it open to try again. */
  async function pressAdd(t: PlotThread, whoId: string, role: string): Promise<void> {
    if (busy || !api.addPlotThreadInvolved) return;
    setBusy(true);
    setSaid(null);
    try {
      await api.addPlotThreadInvolved(refId, { thread: t.id, who: whoId, role });
      setForm(null);
      setSaid({ ok: true, text: w.added });
    } catch (error) {
      setSaid({ ok: false, text: error instanceof ApiError && error.kind === "gone" ? w.addGone : w.couldNot });
    } finally {
      await refresh();
      setBusy(false);
    }
  }

  /** One press that the bot takes: the result says what to show; the list is read again after, and the form closes on success. */
  async function runPress(action: () => Promise<{ ok: boolean; text: string }>): Promise<void> {
    if (busy) return;
    setBusy(true);
    setSaid(null);
    try {
      setSaid(await action());
    } catch (error) {
      setSaid({ ok: false, text: error instanceof ApiError && error.kind === "gone" ? w.addGone : w.couldNot });
    } finally {
      await refresh();
      setBusy(false);
    }
  }

  /** A new thread, of one of the four kinds a card offers, in one of the five states. */
  const pressNew = (name: string, kind: (typeof NEW_KINDS)[number], state: PlotThreadState): Promise<void> =>
    runPress(async () => {
      if (!api.addPlotThread) throw new ApiError("server");
      await api.addPlotThread(refId, { kind, state, name });
      setForm(null);
      return { ok: true, text: w.started };
    });

  /** A link of an event to a thread: it moved the thread along or ended it (#3696, slice 2d). */
  const linkFor = (t: PlotThread, how: "moved" | "ended", pick: { event: string } | { newEvent: string }): Promise<void> =>
    runPress(async () => {
      if (!api.linkPlotThreadEvent) throw new ApiError("server");
      await api.linkPlotThreadEvent(refId, { thread: t.id, how, ...pick });
      setForm(null);
      return { ok: true, text: w.linked };
    });

  /** One thread led to another thread or event (#3696, slice 2d). */
  const ledFor = (t: PlotThread, toId: string): Promise<void> =>
    runPress(async () => {
      if (!api.plotThreadLedTo) throw new ApiError("server");
      try {
        await api.plotThreadLedTo(refId, { from: t.id, to: toId });
      } catch (error) {
        // A thread that is gone is said as such; any other failure (the bot's "failed", a loop or a wrong kind) says nothing was linked.
        if (error instanceof ApiError && error.kind === "gone") throw error;
        return { ok: false, text: w.ledFailed };
      }
      setForm(null);
      return { ok: true, text: w.linked };
    });

  /** A new promise: made open; if the due box has a word, its due note is saved after, and a failure there is said apart. */
  const pressPromise = (name: string, due: string): Promise<void> =>
    runPress(async () => {
      if (!api.addPlotThread) throw new ApiError("server");
      const made = await api.addPlotThread(refId, { kind: "promise", state: "open", name });
      setForm(null);
      if (due.trim() === "") return { ok: true, text: w.promiseDone };
      try {
        if (!api.setPlotThreadDue) throw new ApiError("server");
        await api.setPlotThreadDue(refId, { promise: made.id, due: due.trim() });
        return { ok: true, text: w.promiseDone };
      } catch {
        return { ok: false, text: w.dueFailed };
      }
    });

  const dueFor = (t: PlotThread, due: string): Promise<void> =>
    runPress(async () => {
      if (!api.setPlotThreadDue) throw new ApiError("server");
      await api.setPlotThreadDue(refId, { promise: t.id, due: due.trim() });
      setForm(null);
      return { ok: true, text: w.saved };
    });

  const partyFor = (t: PlotThread, side: "by" | "to", whoId: string): Promise<void> =>
    runPress(async () => {
      if (!api.addPlotThreadParty) throw new ApiError("server");
      await api.addPlotThreadParty(refId, { promise: t.id, side, who: whoId });
      setForm(null);
      return { ok: true, text: w.pickedDone };
    });

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
      {api.addPlotThread && (
        <div class="stack">
          <div class="row">
            <button
              type="button"
              class="button secondary"
              aria-expanded={form === "new"}
              aria-controls={form === "new" ? "plot-new" : undefined}
              disabled={busy}
              onClick={() => setForm(form === "new" ? null : "new")}
            >
              {w.newButton}
            </button>
            <button
              type="button"
              class="button secondary"
              aria-expanded={form === "promise"}
              aria-controls={form === "promise" ? "plot-promise" : undefined}
              disabled={busy}
              onClick={() => setForm(form === "promise" ? null : "promise")}
            >
              {w.promiseButton}
            </button>
          </div>
          {form === "new" && (
            <div id="plot-new">
              <NewThreadForm busy={busy} onSubmit={(name, kind, state) => void pressNew(name, kind, state)} onClose={() => setForm(null)} />
            </div>
          )}
          {form === "promise" && (
            <div id="plot-promise">
              <PromiseForm busy={busy} onSubmit={(name, due) => void pressPromise(name, due)} onClose={() => setForm(null)} />
            </div>
          )}
        </div>
      )}
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
                  api={api}
                  refId={refId}
                  canAdd={!!api.addPlotThreadInvolved}
                  form={form}
                  onForm={setForm}
                  pressDue={(due) => void dueFor(t, due)}
                  pressParty={(side, whoId) => void partyFor(t, side, whoId)}
                  pressLink={(how, pick) => void linkFor(t, how, pick)}
                  pressLed={(toId) => void ledFor(t, toId)}
                  onAddSubmit={(whoId, role) => void pressAdd(t, whoId, role)}
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
  api,
  refId,
  canAdd,
  form,
  onForm,
  pressDue,
  pressParty,
  pressLink,
  pressLed,
  onAddSubmit,
}: {
  thread: PlotThread;
  isOpen: boolean;
  onToggle: () => void;
  changing: boolean;
  canChange: boolean;
  busy: boolean;
  onChange: () => void;
  onPress: (state: PlotThreadState | "check") => void;
  api: AccountApi;
  refId: string;
  canAdd: boolean;
  form: string | null;
  onForm: (key: string | null) => void;
  pressDue: (due: string) => void;
  pressParty: (side: "by" | "to", whoId: string) => void;
  pressLink: (how: "moved" | "ended", pick: { event: string } | { newEvent: string }) => void;
  pressLed: (toId: string) => void;
  onAddSubmit: (whoId: string, role: string) => void;
}) {
  const addKey = `add:${t.id}`;
  const adding = form === addKey;
  const toggle = (key: string) => onForm(form === key ? null : key);
  const historyId = `plot-history-${t.id}`;
  const changeId = `plot-change-${t.id}`;
  const addId = `plot-add-${t.id}`;
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
      {canAdd && (
        <div class="stack">
          <button
            type="button"
            class="button secondary"
            aria-expanded={adding}
            aria-controls={adding ? addId : undefined}
            disabled={busy}
            onClick={() => toggle(addKey)}
          >
            {adding ? w.closeChange : w.addButton}
          </button>
          {adding && (
            <div id={addId}>
              <AddInvolvedForm api={api} refId={refId} threadId={t.id} busy={busy} onSubmit={onAddSubmit} onClose={() => toggle(addKey)} />
            </div>
          )}
        </div>
      )}
      <div class="stack">
        <div class="row">
          <button type="button" class="button secondary" aria-expanded={form === `link:${t.id}`} disabled={busy} onClick={() => toggle(`link:${t.id}`)}>
            {w.linkButton}
          </button>
          <button type="button" class="button secondary" aria-expanded={form === `led:${t.id}`} disabled={busy} onClick={() => toggle(`led:${t.id}`)}>
            {w.ledButton}
          </button>
        </div>
        {form === `link:${t.id}` && (
          <LinkEventForm
            api={api}
            refId={refId}
            threadId={t.id}
            busy={busy}
            onSubmit={(how, pick) => pressLink(how, pick)}
            onClose={() => onForm(null)}
          />
        )}
        {form === `led:${t.id}` && (
          <NameSearch
            api={api}
            refId={refId}
            kinds={LED_KINDS}
            threadId={t.id}
            busy={busy}
            label={w.ledLabel}
            placeholder={w.ledPlaceholder}
            onPick={(id) => pressLed(id)}
            onClose={() => onForm(null)}
          />
        )}
      </div>
      {t.kind === "promise" && (
        <div class="stack">
          <div class="row">
            <button type="button" class="button secondary" aria-expanded={form === `due:${t.id}`} disabled={busy} onClick={() => toggle(`due:${t.id}`)}>
              {w.dueButton}
            </button>
            <button type="button" class="button secondary" aria-expanded={form === `by:${t.id}`} disabled={busy} onClick={() => toggle(`by:${t.id}`)}>
              {w.byButton}
            </button>
            <button type="button" class="button secondary" aria-expanded={form === `to:${t.id}`} disabled={busy} onClick={() => toggle(`to:${t.id}`)}>
              {w.toButton}
            </button>
          </div>
          {form === `due:${t.id}` && (
            <DueForm busy={busy} initial={t.promise?.due ?? ""} onSubmit={pressDue} onClose={() => onForm(null)} />
          )}
          {form === `by:${t.id}` && (
            <NameSearch api={api} refId={refId} kinds={BY_KINDS} threadId={t.id} busy={busy} onPick={(id) => pressParty("by", id)} onClose={() => onForm(null)} />
          )}
          {form === `to:${t.id}` && (
            <NameSearch api={api} refId={refId} kinds={TO_KINDS} threadId={t.id} busy={busy} onPick={(id) => pressParty("to", id)} onClose={() => onForm(null)} />
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

// Kinds that are not a name you can put in a thread: threads and their parts, people cards and deeds (#3696, slice 2b).
const NOT_INVOLVABLE = new Set(["thread", "quest", "mystery", "hook", "goal", "promise", "person", "deed"]);
// Most matches the search list shows at once.
const MATCHES = 8;

/** "Add who's involved": a search over the campaign's names, a required part, and Add. The names are read once, when the form opens. */
function AddInvolvedForm({
  api,
  refId,
  threadId,
  busy,
  onSubmit,
  onClose,
}: {
  api: AccountApi;
  refId: string;
  threadId: string;
  busy: boolean;
  onSubmit: (whoId: string, role: string) => void;
  onClose: () => void;
}) {
  const [names, setNames] = useState<CampaignName[] | null>(null);
  const [query, setQuery] = useState("");
  const [who, setWho] = useState<CampaignName | null>(null);
  const [part, setPart] = useState("");
  // The names could not be read: the form says so, instead of "nothing called that yet" (#4953).
  const [namesFailed, setNamesFailed] = useState(false);

  // Read again each time the form opens: it is mounted when opened and unmounted when closed.
  useEffect(() => {
    let stale = false;
    const load = api.campaignNames ? api.campaignNames(refId) : Promise.resolve([] as CampaignName[]);
    load.then(
      (got) => {
        if (stale) return;
        setNamesFailed(false);
        setNames(got);
      },
      () => {
        if (stale) return;
        setNamesFailed(true);
        setNames([]);
      },
    );
    return () => {
      stale = true;
    };
  }, [api, refId]);

  const needle = query.trim().toLowerCase();
  const matches = (names ?? [])
    .filter((n) => !NOT_INVOLVABLE.has(n.kind) && n.id !== threadId && n.text.toLowerCase().includes(needle))
    .slice(0, MATCHES);
  // The part is optional: a name is enough, and a blank part is sent as empty (#4953).
  const ready = who !== null;

  return (
    <div class="stack" role="group" aria-label={w.addButton}>
      <label class="stack">
        {w.searchLabel}
        <input
          type="search"
          value={query}
          placeholder={w.searchPlaceholder}
          disabled={busy}
          autoComplete="off"
          onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
        />
      </label>
      {names === null ? (
        <p class="muted small">{w.loading}</p>
      ) : namesFailed ? (
        <p class="warn" role="alert">{w.namesFailed}</p>
      ) : matches.length === 0 ? (
        needle !== "" ? <p class="small">{w.noMatch}</p> : null
      ) : (
        <ul class="stack">
          {matches.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                class={who?.id === n.id ? "button" : "button secondary"}
                aria-pressed={who?.id === n.id}
                disabled={busy}
                onClick={() => setWho(n)}
              >
                {n.text}
              </button>
            </li>
          ))}
        </ul>
      )}
      <label class="stack">
        {w.partLabel}
        <input
          type="text"
          value={part}
          maxLength={w.partMax}
          placeholder={w.partPlaceholder}
          disabled={busy}
          autoComplete="off"
          onInput={(e) => setPart((e.target as HTMLInputElement).value)}
        />
      </label>
      <div class="row">
        {w.suggestions.map((s) => (
          <button key={s} type="button" class="button secondary" disabled={busy} onClick={() => setPart(s)}>
            {s}
          </button>
        ))}
      </div>
      <div class="row">
        <button type="button" class="button" disabled={busy || !ready} onClick={() => who && onSubmit(who.id, part.trim())}>
          {w.addSubmit}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={onClose}>
          {w.closeChange}
        </button>
      </div>
      <SavingWait active={busy} stage={w.working} />
    </div>
  );
}

/** A new story thread: a name, one of the four kinds a card offers, and where it is now (#3696, slice 2c). */
function NewThreadForm({
  busy,
  onSubmit,
  onClose,
}: {
  busy: boolean;
  onSubmit: (name: string, kind: (typeof NEW_KINDS)[number], state: PlotThreadState) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<(typeof NEW_KINDS)[number]>("quest");
  const [state, setState] = useState<PlotThreadState>("open");
  const ready = name.trim() !== "";
  return (
    <div class="stack" role="group" aria-label={w.newButton}>
      <label class="stack">
        {w.newNameLabel}
        <input
          type="text"
          value={name}
          maxLength={100}
          placeholder={w.newNamePlaceholder}
          disabled={busy}
          autoComplete="off"
          onInput={(e) => setName((e.target as HTMLInputElement).value)}
        />
      </label>
      <fieldset class="stack" disabled={busy}>
        <legend>{w.newKindLabel}</legend>
        {w.newKinds.map((k) => (
          <label key={k.kind} class="choice">
            <input type="radio" name="plot-new-kind" checked={kind === k.kind} onChange={() => setKind(k.kind)} />
            <span>{k.label}</span>
          </label>
        ))}
      </fieldset>
      <label class="stack">
        {w.newStateLabel}
        <select value={state} disabled={busy} onChange={(e) => setState((e.target as HTMLSelectElement).value as PlotThreadState)}>
          {plotThreadStates.map((s) => (
            <option key={s} value={s}>
              {w.stateWord(false, s)}
            </option>
          ))}
        </select>
      </label>
      <div class="row">
        <button type="button" class="button" disabled={busy || !ready} onClick={() => onSubmit(name.trim(), kind, state)}>
          {w.createIt}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={onClose}>
          {w.closeChange}
        </button>
      </div>
      <SavingWait active={busy} stage={w.working} />
    </div>
  );
}

/** A new promise: what was promised, and an optional due note (#3696, slice 2c). */
function PromiseForm({ busy, onSubmit, onClose }: { busy: boolean; onSubmit: (name: string, due: string) => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [due, setDue] = useState("");
  const ready = name.trim() !== "";
  return (
    <div class="stack" role="group" aria-label={w.promiseButton}>
      <label class="stack">
        {w.promiseNameLabel}
        <input
          type="text"
          value={name}
          maxLength={100}
          placeholder={w.promiseNamePlaceholder}
          disabled={busy}
          autoComplete="off"
          onInput={(e) => setName((e.target as HTMLInputElement).value)}
        />
      </label>
      <label class="stack">
        {w.promiseDueLabel}
        <input
          type="text"
          value={due}
          maxLength={120}
          placeholder={w.promiseDuePlaceholder}
          disabled={busy}
          autoComplete="off"
          onInput={(e) => setDue((e.target as HTMLInputElement).value)}
        />
      </label>
      <div class="row">
        <button type="button" class="button" disabled={busy || !ready} onClick={() => onSubmit(name.trim(), due)}>
          {w.addSubmit}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={onClose}>
          {w.closeChange}
        </button>
      </div>
      <SavingWait active={busy} stage={w.working} />
    </div>
  );
}

/** A promise's due note: the box starts with the note it has; an empty note clears it (#3696, slice 2c). */
function DueForm({ busy, initial, onSubmit, onClose }: { busy: boolean; initial: string; onSubmit: (due: string) => void; onClose: () => void }) {
  const [due, setDue] = useState(initial);
  return (
    <div class="stack" role="group" aria-label={w.dueButton}>
      <label class="stack">
        {w.dueLabel}
        <input
          type="text"
          value={due}
          maxLength={120}
          placeholder={w.promiseDuePlaceholder}
          disabled={busy}
          autoComplete="off"
          onInput={(e) => setDue((e.target as HTMLInputElement).value)}
        />
      </label>
      <div class="row">
        <button type="button" class="button" disabled={busy} onClick={() => onSubmit(due)}>
          {w.dueSave}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={onClose}>
          {w.closeChange}
        </button>
      </div>
      <SavingWait active={busy} stage={w.working} />
    </div>
  );
}

/** A name search for a promise's party: only the kinds given, not the thread itself, at most eight; picking one adds it. */
function NameSearch({
  api,
  refId,
  kinds,
  threadId,
  busy,
  label = w.searchLabel,
  placeholder = w.searchPlaceholder,
  onPick,
  onClose,
}: {
  api: AccountApi;
  refId: string;
  kinds: string[];
  threadId: string;
  busy: boolean;
  label?: string;
  placeholder?: string;
  onPick: (whoId: string) => void;
  onClose: () => void;
}) {
  const [names, setNames] = useState<CampaignName[] | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let stale = false;
    const load = api.campaignNames ? api.campaignNames(refId) : Promise.resolve([] as CampaignName[]);
    load.then(
      (got) => {
        if (!stale) setNames(got);
      },
      () => {
        if (!stale) setNames([]);
      },
    );
    return () => {
      stale = true;
    };
  }, [api, refId]);

  const needle = query.trim().toLowerCase();
  const matches = (names ?? [])
    .filter((n) => kinds.includes(n.kind) && n.id !== threadId && n.text.toLowerCase().includes(needle))
    .slice(0, MATCHES);

  return (
    <div class="stack" role="group" aria-label={label}>
      <label class="stack">
        {label}
        <input
          type="search"
          value={query}
          placeholder={placeholder}
          disabled={busy}
          autoComplete="off"
          onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
        />
      </label>
      {names === null ? (
        <p class="muted small">{w.loading}</p>
      ) : matches.length === 0 ? (
        needle !== "" ? <p class="small">{w.noMatch}</p> : null
      ) : (
        <ul class="stack">
          {matches.map((n) => (
            <li key={n.id}>
              <button type="button" class="button secondary" disabled={busy} onClick={() => onPick(n.id)}>
                {n.text}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div class="row">
        <button type="button" class="button secondary" disabled={busy} onClick={onClose}>
          {w.closeChange}
        </button>
      </div>
      <SavingWait active={busy} stage={w.working} />
    </div>
  );
}

/** "Link an event": a search over the campaign's events, or a new event made on the spot; then whether it moved the thread along or ended it (#3696, slice 2d). */
function LinkEventForm({
  api,
  refId,
  threadId,
  busy,
  onSubmit,
  onClose,
}: {
  api: AccountApi;
  refId: string;
  threadId: string;
  busy: boolean;
  onSubmit: (how: "moved" | "ended", pick: { event: string } | { newEvent: string }) => void;
  onClose: () => void;
}) {
  const [names, setNames] = useState<CampaignName[] | null>(null);
  const [query, setQuery] = useState("");
  // The event picked: an existing one (by id) or a new one (by its typed name), with the words to show for it.
  const [pick, setPick] = useState<{ pick: { event: string } | { newEvent: string }; text: string } | null>(null);

  useEffect(() => {
    let stale = false;
    const load = api.campaignNames ? api.campaignNames(refId) : Promise.resolve([] as CampaignName[]);
    load.then(
      (got) => {
        if (!stale) setNames(got);
      },
      () => {
        if (!stale) setNames([]);
      },
    );
    return () => {
      stale = true;
    };
  }, [api, refId]);

  const typed = query.trim();
  const needle = typed.toLowerCase();
  const events = (names ?? [])
    .filter((n) => n.kind === "event" && n.id !== threadId && n.text.toLowerCase().includes(needle))
    .slice(0, MATCHES);

  return (
    <div class="stack" role="group" aria-label={w.linkButton}>
      {pick === null ? (
        <>
          <label class="stack">
            {w.eventLabel}
            <input
              type="search"
              value={query}
              maxLength={w.eventMax}
              placeholder={w.eventPlaceholder}
              disabled={busy}
              autoComplete="off"
              onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
            />
          </label>
          {names === null ? (
            <p class="muted small">{w.loading}</p>
          ) : events.length === 0 ? (
            typed !== "" ? (
              <div class="row">
                <button type="button" class="button secondary" disabled={busy} onClick={() => setPick({ pick: { newEvent: typed }, text: typed })}>
                  {w.newEventChoice(typed)}
                </button>
              </div>
            ) : null
          ) : (
            <ul class="stack">
              {events.map((n) => (
                <li key={n.id}>
                  <button type="button" class="button secondary" disabled={busy} onClick={() => setPick({ pick: { event: n.id }, text: n.text })}>
                    {n.text}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <div class="stack">
          <p>
            <strong>{pick.text}</strong>
          </p>
          <p>{w.howQuestion}</p>
          <div class="row">
            <button type="button" class="button" disabled={busy} onClick={() => onSubmit("moved", pick.pick)}>
              {w.moved}
            </button>
            <button type="button" class="button secondary" disabled={busy} onClick={() => onSubmit("ended", pick.pick)}>
              {w.ended}
            </button>
          </div>
        </div>
      )}
      <div class="row">
        <button type="button" class="button secondary" disabled={busy} onClick={onClose}>
          {w.closeChange}
        </button>
      </div>
      <SavingWait active={busy} stage={w.working} />
    </div>
  );
}
