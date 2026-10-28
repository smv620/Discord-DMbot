/** @jsxImportSource preact */
// "After the session: things to check" (#2661; docs/STORY_MEMORY.md 9a), for a session's DM only: the minor
// contradictions PlotBot found and what the party has wrong (disguised, misunderstood, simply false). The API
// refuses anyone else (a player gets "not allowed" and this renders nothing: it is full of spoilers). It holds
// only DMbot's own words and placeholders, never a person's words. Each item gets four answers (That's
// intended, Fix the DM's view, Not a problem, Remind me next session); each is queued for the bot and can be
// undone. While the report is made the shared wait panel shows the stage, how far, how long, and when it last
// moved (owner rule 2026-10-23), backing off while nothing moves and stopping after 30 minutes. DMbot lists
// and the DM decides: nothing here changes the story by itself.
import { useEffect, useRef, useState } from "preact/hooks";

import { reportWords as w } from "../content/report-words";
import { stageWords } from "../content/work-progress";
import {
  ApiError,
  type AccountApi,
  type ReportAction,
  type ReportAnswer,
  type ReportContradiction,
  type ReportDifference,
  type ReportEvidence,
  type SessionReport,
} from "./api";
import WorkProgress from "./WorkProgress";

const POLL_START_MS = 3_000;
const POLL_MAX_MS = 15_000;
/** After this long with the report still being made the page stops looking and says so. */
export const REPORT_WATCH_LIMIT_MS = 30 * 60_000;

// An unknown kind gets the general sentence, never the code itself.
const say = (error: unknown): string => {
  const kind = error instanceof ApiError ? error.kind : "server";
  return w.errors[kind] ?? (w.errors["server"] as string);
};

function evidenceWords(e: ReportEvidence): string {
  const kind = w.evidenceKinds[e.kind] ?? w.evidenceKinds["claim"] ?? "";
  // A transcript line is a person's words: it is never quoted here, even while it exists (#2661; the API
  // should not send one, and the page does not trust it to). Only DMbot's own kinds are quoted.
  if (e.kind === "line") return e.available ? w.lineHere(e.session) : w.gone(kind, e.session);
  // The words are gone: say the session and the kind, never the words or who said them.
  return e.available && e.quote ? w.there(kind, e.session, e.quote) : w.gone(kind, e.session);
}

export default function RecapReport({ api, campaignRef, sessionAt }: { api: AccountApi; campaignRef: string; sessionAt: number }) {
  const [report, setReport] = useState<SessionReport | null>(null);
  const [hidden, setHidden] = useState(false);
  const latest = useRef(0); // the newest look's number: an older, slower answer is ignored
  const inFlight = useRef(false); // answers go one at a time, even two clicks in one tick
  const fails = useRef(0); // looks in a row that failed while it was being made
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // the item being answered (or "make")
  const [stopped, setStopped] = useState(false);
  // One polite place for results, so a screen reader hears "Saved. 3 left." once, not every line that changes.
  const [said, setSaid] = useState("");
  const box = useRef<HTMLElement>(null);
  const started = useRef(0);

  const load = async (): Promise<SessionReport | null> => {
    if (!api.sessionReport) return null;
    const mine = ++latest.current;
    try {
      const got = await api.sessionReport(campaignRef, sessionAt);
      if (mine !== latest.current) return null;
      setReport(got);
      setProblem("");
      return got;
    } catch (error) {
      if (mine !== latest.current) return null;
      const kind = error instanceof ApiError ? error.kind : "";
      // A player, another campaign's DM or an unknown session sees nothing at all.
      if (kind === "not-allowed" || kind === "not-found") setHidden(true);
      else setProblem(say(error));
      return null;
    }
  };

  useEffect(() => {
    // Another session or campaign: nothing of the old one stays.
    latest.current += 1;
    setReport(null);
    setHidden(false);
    setProblem("");
    setStopped(false);
    setSaid("");
    started.current = 0;
    fails.current = 0;
    void load();
    return () => {
      latest.current += 1; // (an answer still on its way is ignored)
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef, sessionAt]);

  // While it is made: look again, backing off while nothing moves, and stop after 30 minutes.
  const making = report?.state === "making";
  const hiddenNow = useRef(false);
  hiddenNow.current = hidden;
  useEffect(() => {
    if (!making) return;
    if (!started.current) started.current = Date.now();
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let wait = POLL_START_MS;
    let last = "";
    const tick = async (): Promise<void> => {
      const got = await load();
      if (!live) return;
      if (!got) {
        // One failed look is not the end of the wait: keep looking, slower, and say so when it goes on.
        if (hiddenNow.current) return;
        fails.current += 1;
        wait = Math.min(wait * 2, POLL_MAX_MS);
        if (Date.now() - started.current > REPORT_WATCH_LIMIT_MS) return setStopped(true);
        timer = setTimeout(() => void tick(), wait);
        return;
      }
      fails.current = 0;
      if (got.state !== "making") return;
      const seen = `${got.progress?.stage ?? ""}:${got.progress?.updatedAt ?? ""}:${got.progress?.done ?? ""}`;
      wait = seen !== last ? POLL_START_MS : Math.min(wait * 2, POLL_MAX_MS);
      last = seen;
      if (Date.now() - started.current > REPORT_WATCH_LIMIT_MS) return setStopped(true);
      timer = setTimeout(() => void tick(), wait);
    };
    timer = setTimeout(() => void tick(), POLL_START_MS);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [making]);

  // Nothing at all until the API has said this is a DM's report: no heading, no lead, for anyone else.
  if (hidden || !api.sessionReport || (!report && !problem)) return null;
  if (!report) {
    return (
      <p class="error" role="alert">
        {problem}
      </p>
    );
  }

  const make = async (): Promise<void> => {
    if (!api.makeSessionReport) return;
    setBusy("make");
    setProblem("");
    started.current = Date.now();
    setStopped(false);
    try {
      setReport(await api.makeSessionReport(campaignRef, sessionAt));
    } catch (error) {
      setProblem(say(error));
    } finally {
      setBusy(null);
    }
  };

  const answer = async (id: string, action: ReportAction | "undo"): Promise<void> => {
    if (!api.answerReport || busy || inFlight.current) return;
    inFlight.current = true;
    setBusy(id);
    setProblem("");
    setSaid("");
    let ok = false;
    try {
      await api.answerReport(campaignRef, sessionAt, id, action);
      ok = true;
    } catch (error) {
      setProblem(say(error));
      // Already answered or gone: the list is read again so it matches the truth, and a refusal that
      // leaves it waiting (cannot write) leaves the item where it was.
    } finally {
      const got = await load();
      inFlight.current = false;
      setBusy(null);
      if (ok && got) setSaid(action === "undo" ? w.undone(got.counts.open) : w.saved(got.counts.open));
      // The button that was pressed is gone: keep the keyboard where the DM was working.
      setTimeout(() => {
        const sel = action === "undo" ? `[data-first-answer="${id}"]` : `[data-undo="${id}"]`;
        box.current?.querySelector<HTMLElement>(sel)?.focus();
      }, 0);
    }
  };

  const failed = report?.state === "failed";
  const p = report?.progress;
  return (
    <section class="stack" aria-labelledby="report-h" data-report ref={box}>
      <h2 id="report-h">{w.heading}</h2>
      <p class="small muted">{w.lead}</p>
      <p class="visually-hidden" role="status" aria-live="polite">
        {said}
      </p>
      {problem && (
        <p class="error" role="alert">
          {problem}
        </p>
      )}
      {report?.state === "none" && (
        <div class="stack">
          <p>{w.none}</p>
          <button type="button" class="button" disabled={busy === "make"} aria-busy={busy === "make"} onClick={() => void make()}>
            {w.make}
          </button>
        </div>
      )}
      {(making || failed) && (
        <div class="stack">
          <WorkProgress
            stage={stageWords(w.stages, p?.stage)}
            done={p?.done}
            total={p?.total}
            unit={p?.unit ?? undefined}
            startedAt={p?.startedAt ?? (started.current ? started.current / 1000 : undefined)}
            updatedAt={p?.updatedAt}
            serverNow={p?.serverNow}
            state={failed ? "failed" : stopped ? "stalled" : (p?.state ?? "running")}
            frozen={stopped}
            reason={failed ? p?.reason : undefined}
            whatToDo={failed ? w.failed : undefined}
          />
          {failed && (
            <p class="small">
              <a href="/hello">{w.sayHello}</a>
            </p>
          )}
          {stopped && <p role="status">{w.stoppedWatching}</p>}
          {failed && (
            <button type="button" class="button secondary" disabled={busy === "make"} onClick={() => void make()}>
              {w.makeAgain}
            </button>
          )}
        </div>
      )}
      {report?.state === "ready" && <Ready report={report} busy={busy} onAnswer={answer} campaignRef={campaignRef} />}
    </section>
  );
}

function Ready({
  report,
  busy,
  onAnswer,
  campaignRef,
}: {
  report: SessionReport;
  busy: string | null;
  onAnswer: (id: string, action: ReportAction | "undo") => Promise<void>;
  campaignRef: string;
}) {
  const { counts, contradictions, differences } = report;
  const total = contradictions.length + differences.disguised.length + differences.misunderstood.length + differences.false.length;
  if (total === 0) return <p>{w.empty}</p>;
  return (
    <div class="stack">
      <p>{w.counts(counts)}</p>
      {contradictions.length > 0 && (
        <section class="stack" aria-labelledby="report-c">
          <h3 id="report-c">{w.contradictionsHeading}</h3>
          <p class="small muted">{w.contradictionsHint}</p>
          <ul class="stack">
            {contradictions.map((c) => (
              <ContradictionItem key={c.id} item={c} busy={busy} onAnswer={onAnswer} campaignRef={campaignRef} />
            ))}
          </ul>
        </section>
      )}
      {(["disguised", "misunderstood", "false"] as const).some((g) => differences[g].length > 0) && (
        <section class="stack" aria-labelledby="report-d">
          <h3 id="report-d">{w.differencesHeading}</h3>
          <p class="small muted">{w.differencesHint}</p>
          {(["disguised", "misunderstood", "false"] as const).map(
            (g) =>
              differences[g].length > 0 && (
                <section key={g} class="stack" aria-labelledby={`report-${g}`}>
                  <h4 id={`report-${g}`}>{w.groups[g]}</h4>
                  <p class="small muted">{w.groupHints[g]}</p>
                  <ul class="stack">
                    {differences[g].map((d) => (
                      <DifferenceItem key={d.id} item={d} busy={busy} onAnswer={onAnswer} campaignRef={campaignRef} />
                    ))}
                  </ul>
                </section>
              ),
          )}
        </section>
      )}
    </div>
  );
}

function Evidence({ list }: { list: ReportEvidence[] }) {
  if (list.length === 0) return null;
  return (
    <details class="small">
      <summary>{w.evidence}</summary>
      <ul>
        {list.map((e, i) => (
          <li key={i}>{evidenceWords(e)}</li>
        ))}
      </ul>
    </details>
  );
}

function Answers({
  id,
  answer,
  busy,
  onAnswer,
  open,
  campaignRef,
}: {
  id: string;
  answer: ReportAnswer | null;
  busy: string | null;
  onAnswer: (id: string, action: ReportAction | "undo") => Promise<void>;
  open: { a: string; b: string } | null;
  campaignRef: string;
}) {
  const mine = busy === id;
  if (answer) {
    return (
      <div class="row">
        <p class="small">{w.answered(answer.action)}</p>
        {open && answer.action === "fix" && (
          <a class="button secondary" href={`/account/web/${encodeURIComponent(campaignRef)}`}>
            {w.openConnection}
          </a>
        )}
        <button
          type="button"
          class="button secondary"
          disabled={!!busy}
          aria-describedby={`report-item-${id}`}
          data-undo={id}
          onClick={() => void onAnswer(id, "undo")}
        >
          {w.undo}
        </button>
      </div>
    );
  }
  const actions: ReportAction[] = open ? ["intended", "fix", "not_a_problem", "remind"] : ["intended", "not_a_problem", "remind"];
  return (
    <div class="row" aria-busy={mine}>
      {actions.map((a) => (
        <button
          key={a}
          type="button"
          class="button secondary"
          disabled={!!busy}
          aria-describedby={`report-item-${id}`}
          {...(a === actions[0] ? { "data-first-answer": id } : {})}
          onClick={() => void onAnswer(id, a)}
        >
          {w.actions[a]}
        </button>
      ))}
      {mine && <span class="small muted">{w.working}</span>}
    </div>
  );
}

function ContradictionItem({
  item,
  busy,
  onAnswer,
  campaignRef,
}: {
  item: ReportContradiction;
  busy: string | null;
  onAnswer: (id: string, action: ReportAction | "undo") => Promise<void>;
  campaignRef: string;
}) {
  return (
    <li class={item.answer ? "stack muted" : "stack"}>
      <p id={`report-item-${item.id}`}>{item.reason}</p>
      <blockquote class="stack">
        <p>
          <strong>{w.first}:</strong> {item.first.text}
        </p>
        <p class="small muted">
          {w.view(item.first.view)} · {w.fromSession(item.first.session)}
        </p>
        <Evidence list={item.first.evidence} />
      </blockquote>
      <blockquote class="stack">
        <p>
          <strong>{w.second}:</strong> {item.second.text}
        </p>
        <p class="small muted">
          {w.view(item.second.view)} · {w.fromSession(item.second.session)}
        </p>
        <Evidence list={item.second.evidence} />
      </blockquote>
      {!item.gameTimeKnown && <p class="small">{w.gameTimesUnknown}</p>}
      {item.remindedFrom ? <p class="small">{w.reminded(item.remindedFrom)}</p> : null}
      <Answers id={item.id} answer={item.answer} busy={busy} onAnswer={onAnswer} open={null} campaignRef={campaignRef} />
    </li>
  );
}

function DifferenceItem({
  item,
  busy,
  onAnswer,
  campaignRef,
}: {
  item: ReportDifference;
  busy: string | null;
  onAnswer: (id: string, action: ReportAction | "undo") => Promise<void>;
  campaignRef: string;
}) {
  return (
    <li class={item.answer ? "stack muted" : "stack"}>
      <p id={`report-item-${item.id}`}>{item.reason}</p>
      <p>
        <strong>{w.believes}:</strong> {item.believes}
      </p>
      <p>
        <strong>{w.truth}:</strong> {item.truth}
      </p>
      <p class="small muted">{w.view(item.view)}</p>
      <Evidence list={item.evidence} />
      {item.remindedFrom ? <p class="small">{w.reminded(item.remindedFrom)}</p> : null}
      <Answers id={item.id} answer={item.answer} busy={busy} onAnswer={onAnswer} open={item.open} campaignRef={campaignRef} />
    </li>
  );
}
