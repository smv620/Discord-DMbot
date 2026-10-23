/** @jsxImportSource preact */
// The "Plot check" on a recap's page (#2124; core #2122, #2123): PlotBot's questions about the recap,
// each with the recap's sentence and the earlier note it may not match. The DM answers every one:
// fix the recap (the words go to the "Tell DMbot what to fix" box), talk it through with PlotBot,
// "not a problem" (with a note), or leave it as is. Leaving it is always allowed, so the DM decides.
// A change PlotBot suggests is kept only when the DM presses Save. Approve waits until every question
// has an answer (the page tells the parent why through `onBlocked`). The page asks again after an edit.
// Shown only while RECAPS_LIVE and PLOT_CHECK_LIVE are true, to the DM; never on the public recap link.
import { useEffect, useRef, useState } from "preact/hooks";

import { PLOT_CHAT_MAX_LETTERS, PLOT_NOTE_MAX_LETTERS, plotWords as words } from "../content/recap-plot";
import { shortDateTime } from "../content/account";
import { PLOT_CHECK_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type PlotAnswerKind, type PlotCheck, type PlotIssue, type PlotMessage } from "./api";
import { keepFocus } from "./focus";

const POLL_MS = 2000;
const POLL_LIMIT = 30; // about a minute: the check is one careful AI call

export default function RecapPlotCheck({
  api,
  refId,
  text,
  onBlocked,
  onFix,
  reloadKey = 0,
}: {
  api: AccountApi;
  refId: string;
  /** The recap's text: when it changes, the check runs again on the change. */
  text: string;
  /** Why Approve must wait, or null when it need not. */
  onBlocked: (why: string | null) => void;
  /** "Fix the recap": the words to put in the fix box. */
  onFix: (words: string) => void;
  /** Changes when the page should read the check again (Approve said a question is still open). */
  reloadKey?: number;
}) {
  const [check, setCheck] = useState<PlotCheck | null>(null);
  const [failed, setFailed] = useState(false);
  const [gaveUp, setGaveUp] = useState(false); // the check never finished: Approve must not stay locked
  const [tick, setTick] = useState(0); // one more look after each one, whether it worked or not
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [talking, setTalking] = useState<string | null>(null);
  const [noting, setNoting] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [fixedFor, setFixedFor] = useState<Set<string>>(new Set());
  const firstText = useRef(text);
  const changed = useRef(false); // the text changed since the last finished check
  const looks = useRef(0);
  const live = RECAPS_LIVE && PLOT_CHECK_LIVE && !!api.plotCheck && !!api.answerPlotIssue;

  const read = async (): Promise<PlotCheck | null> => {
    try {
      const got = await api.plotCheck!(refId);
      if (got.state === "ready") changed.current = false;
      setCheck(got);
      setFailed(false);
      return got;
    } catch {
      setFailed(check === null);
      return null;
    }
  };

  useEffect(() => {
    if (live) void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, refId]);

  useEffect(() => {
    if (live && reloadKey > 0) void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  // An edit: say so, and look again until the check is ready.
  useEffect(() => {
    if (!live || text === firstText.current) return;
    firstText.current = text;
    looks.current = 0;
    changed.current = true;
    setGaveUp(false);
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  const checking = check?.state === "checking";
  // Look again every two seconds while the check runs. A look that fails (a dropped connection) does not
  // end the watching: the next one is still made. After about a minute the page stops and says so, and
  // Approve opens (the API still refuses an approval if a question it knows of is open).
  useEffect(() => {
    if (!live || !checking || gaveUp) return;
    const timer = setTimeout(async () => {
      looks.current += 1;
      if (looks.current >= POLL_LIMIT) {
        setGaveUp(true);
        return;
      }
      await read();
      setTick((n) => n + 1);
    }, POLL_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, checking, gaveUp, tick]);

  const open = (check?.issues ?? []).filter((i) => i.answer === null).length;
  // Approve waits for the check and for every question. (The API's own `approveBlocked` is as of the
  // last look, so it is not used here: it would stay true after the DM answers. Approve itself is
  // refused by the API if a question is still open.)
  const why = !live || check === null || gaveUp
    ? null
    : checking
      ? words.waitingCheck
      : open > 0
        ? words.waitingOn(open)
        : null;
  useEffect(() => {
    onBlocked(why);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [why]);
  useEffect(() => () => onBlocked(null), []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!live) return null;
  if (failed) {
    return (
      <section class="stack" aria-labelledby="plot-heading">
        <h3 id="plot-heading">{words.heading}</h3>
        <p class="warn" role="alert">
          {words.loadFailed}
        </p>
      </section>
    );
  }
  if (check === null) return null;

  const fail = (id: string, error: unknown) => {
    const kind = error instanceof ApiError ? error.kind : "server";
    if (kind === "stale" || kind === "not-found") void read(); // the check moved on: show what is true now
    setProblems((was) => ({ ...was, [id]: words.problem[kind] ?? words.problem.server ?? "" }));
  };
  const keep = (next: PlotIssue) => {
    setCheck((was) => (was ? { ...was, issues: was.issues.map((i) => (i.id === next.id ? next : i)) } : was));
  };
  const run = async (id: string, work: () => Promise<void>) => {
    if (busy.has(id)) return;
    setBusy((was) => new Set(was).add(id));
    setProblems((was) => ({ ...was, [id]: "" }));
    try {
      await work();
    } catch (error) {
      fail(id, error);
    } finally {
      setBusy((was) => {
        const next = new Set(was);
        next.delete(id);
        return next;
      });
    }
  };
  const answer = (issue: PlotIssue, kind: PlotAnswerKind, note?: string) =>
    run(issue.id, async () => {
      keep(await api.answerPlotIssue!(refId, issue.id, note ? { kind, note } : { kind }));
      setSaid(words.saved(Math.max(open - (issue.answer === null ? 1 : 0), 0)));
    });

  return (
    <section class="stack" aria-labelledby="plot-heading">
      <h3 id="plot-heading" tabIndex={-1}>
        {words.heading}
      </h3>
      <p class="muted">{words.lead}</p>
      <p role="status" aria-live="polite" class="small">
        {checking && !gaveUp ? (changed.current ? words.checkingChange : words.checking) : said}
      </p>
      {gaveUp && <p class="note" role="status">{words.gaveUp}</p>}
      {check.state === "unavailable" && <p class="note" role="status">{words.unavailable}</p>}
      {check.state === "ready" && check.issues.length === 0 && <p>{words.none}</p>}
      {check.state === "ready" && (
        <ul class="plot-issues">
          {check.issues.map((issue, index) => (
            <li key={issue.id}>
              <Issue
                issue={issue}
                n={index + 1}
                busy={busy.has(issue.id)}
                problem={problems[issue.id] ?? ""}
                talking={talking === issue.id}
                noting={noting === issue.id}
                onAnswer={(kind, note) => void answer(issue, kind, note)}
                onFix={() => {
                  // Only the recap's own sentence goes to the box, never DMbot's reason: the DM says what
                  // should change. Nothing is answered until the recap is sent and checked again.
                  onFix(words.fixPrefill(issue.sentenceText, issue.source.session));
                  setFixedFor((was) => new Set(was).add(issue.id));
                }}
                fixedAsked={fixedFor.has(issue.id)}
                onTalk={(on) => setTalking(on ? issue.id : null)}
                onNote={(on) => setNoting(on ? issue.id : null)}
                api={api}
                refId={refId}
                onSaved={() => void read()}
              />
            </li>
          ))}
        </ul>
      )}
      <details>
        <summary>{words.versions}</summary>
        {check.versions.length === 0 ? (
          <p class="small muted">{words.noVersions}</p>
        ) : (
          <ul class="small">
            {check.versions.map((v) => (
              <li key={v.version}>{words.versionLine(v.version, shortDateTime(new Date(v.acceptedAt * 1000).toISOString()))}</li>
            ))}
          </ul>
        )}
      </details>
    </section>
  );
}

function Issue({
  issue,
  n,
  busy,
  problem,
  talking,
  noting,
  onAnswer,
  onFix,
  fixedAsked,
  onTalk,
  onNote,
  api,
  refId,
  onSaved,
}: {
  issue: PlotIssue;
  n: number;
  busy: boolean;
  problem: string;
  talking: boolean;
  noting: boolean;
  onAnswer: (kind: PlotAnswerKind, note?: string) => void;
  onFix: () => void;
  fixedAsked: boolean;
  onTalk: (on: boolean) => void;
  onNote: (on: boolean) => void;
  api: AccountApi;
  refId: string;
  /** A suggested note was saved: read the check again, the bot may have answered the question. */
  onSaved: () => void;
}) {
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [chatProblem, setChatProblem] = useState("");
  const [messages, setMessages] = useState<PlotMessage[]>([]);
  const [chatSlow, setChatSlow] = useState(false);
  const looks = useRef(0);
  const savedSeen = useRef(new Set<string>());
  const titleId = `plot-issue-${issue.id}`;
  const level = words.level[issue.level] ?? "";
  const kind = words.kind[issue.kind] ?? "";
  const answered = issue.answer !== null;
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const chatRef = useRef<HTMLTextAreaElement>(null);

  const readChat = async (): Promise<PlotMessage[] | null> => {
    try {
      const got = await api.plotChat!(refId, issue.id);
      setMessages(got);
      return got;
    } catch (error) {
      const code = error instanceof ApiError ? error.kind : "server";
      setChatProblem(words.problem[code] ?? words.problem["server"] ?? "");
      return null;
    }
  };
  // The talk is read when it is opened, and again every two seconds while PlotBot has a message to answer
  // or a note to save (about two minutes at most, then it says so).
  const waiting = messages.some((m) => (m.role === "dm" && (m.state === "waiting" || m.state === "working")) || m.proposals.some((p) => p.state === "saving"));
  useEffect(() => {
    if (talking && api.plotChat) void readChat();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [talking]);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!talking || !waiting || chatSlow) {
      if (!waiting) looks.current = 0;
      return;
    }
    const timer = setTimeout(async () => {
      looks.current += 1;
      if (looks.current >= 60) {
        setChatSlow(true);
        return;
      }
      await readChat();
      setTick((n) => n + 1);
    }, 2000);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [talking, waiting, chatSlow, tick]);
  // A note that was saved: the question may have been answered by it.
  useEffect(() => {
    for (const m of messages)
      for (const p of m.proposals)
        if (p.state === "saved" && !savedSeen.current.has(p.id)) {
          savedSeen.current.add(p.id);
          onSaved();
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  const send = async () => {
    if (sending || message.trim() === "" || !api.plotChatSend) return;
    setSending(true);
    setChatProblem("");
    setChatSlow(false);
    looks.current = 0;
    try {
      await api.plotChatSend(refId, issue.id, message.trim());
      setMessage("");
      await readChat();
    } catch (error) {
      const code = error instanceof ApiError ? error.kind : "server";
      if (code === "stale") onSaved(); // the recap changed: read the check again
      setChatProblem(words.problem[code] ?? words.problem["server"] ?? "");
    } finally {
      setSending(false);
    }
  };
  const decide = async (proposal: string, action: "save" | "decline") => {
    if (sending || !api.decidePlotProposal) return;
    setSending(true);
    setChatProblem("");
    try {
      await api.decidePlotProposal(refId, issue.id, proposal, action);
      await readChat();
    } catch (error) {
      const code = error instanceof ApiError ? error.kind : "server";
      if (code === "stale") onSaved();
      setChatProblem(words.problem[code] ?? words.problem["server"] ?? "");
    } finally {
      setSending(false);
    }
  };

  return (
    <div class="panel stack" role="group" aria-labelledby={titleId}>
      <h4 id={titleId} tabIndex={-1}>
        {kind}
        <span class="small muted"> · {level}</span>
      </h4>
      <p>{issue.reason}</p>
      <p class="small">
        {words.recapSays} <mark>{issue.sentenceText}</mark>
      </p>
      <blockquote class="small">
        <span class="muted">{words.earlier(issue.source.session)}</span> {issue.source.text}
      </blockquote>
      {answered ? (
        <p role="status" class="small">
          {words.yourAnswer[issue.answer!.kind]}
          {issue.answer!.note ? ` “${issue.answer!.note}”` : ""}
        </p>
      ) : null}
      <div class="row">
        <button type="button" class="button secondary" aria-label={words.answerFor(words.answers.fix, n)} onClick={onFix}>
          {words.answers.fix}
        </button>
        <button
          type="button"
          class="button secondary"
          aria-expanded={talking}
          aria-label={words.answerFor(words.answers.talk, n)}
          onClick={() => {
            onTalk(!talking);
            if (!talking) setTimeout(() => chatRef.current?.focus(), 0);
          }}
        >
          {words.answers.talk}
        </button>
        <button
          type="button"
          class="button secondary"
          aria-expanded={noting}
          aria-label={words.answerFor(words.answers.fine, n)}
          onClick={() => {
            onNote(!noting);
            if (!noting) setTimeout(() => noteRef.current?.focus(), 0);
          }}
        >
          {words.answers.fine}
        </button>
        <button type="button" class="button secondary" disabled={busy} aria-label={words.answerFor(words.answers.leave, n)} onClick={() => onAnswer("leave")}>
          {words.answers.leave}
        </button>
      </div>
      {fixedAsked && <p class="small muted">{words.fixed}</p>}
      {noting && (
        <div class="stack">
          <label class="field">
            {words.noteLabel}
            <textarea
              ref={noteRef}
              rows={2}
              maxLength={PLOT_NOTE_MAX_LETTERS}
              value={note}
              onInput={(e) => setNote((e.target as HTMLTextAreaElement).value)}
            />
          </label>
          {PLOT_NOTE_MAX_LETTERS - note.length <= 50 && <p class="small muted">{words.noteLeft(PLOT_NOTE_MAX_LETTERS - note.length)}</p>}
          <button
            type="button"
            class="button"
            disabled={busy}
            aria-label={`${words.noteSave}, question ${n}`}
            onClick={() => {
              onAnswer("fine", note.trim() === "" ? undefined : note.trim());
              onNote(false);
              setNote("");
            }}
          >
            {words.noteSave}
          </button>
        </div>
      )}
      {talking && (
        <div class="stack">
          <h5>{words.chatHeading}</h5>
          <p class="small muted">{words.chatLead}</p>
          <ul class="plot-chat">
            {messages.map((m) => (
              <li key={m.id}>
                <strong>{m.role === "dm" ? words.chatYou : words.chatBot}:</strong> {m.text}
                {m.role === "dm" && (m.state === "waiting" || m.state === "working") && (
                  <span class="small muted"> {words.thinking}</span>
                )}
                {m.state === "failed" && (
                  <span class="small warn"> {words.chatFailed[m.error] ?? words.chatFailed["failed"]}</span>
                )}
                {m.proposals.map((p) => (
                  <div key={p.id} class="stack small">
                    <p>
                      {words.proposal} <em>{p.label}</em>
                    </p>
                    {p.state === "open" ? (
                      <div class="row">
                        <button type="button" class="button" disabled={sending} aria-label={`${words.save}: ${p.label}`} onClick={() => void decide(p.id, "save")}>
                          {words.save}
                        </button>
                        <button type="button" class="button secondary" disabled={sending} aria-label={`${words.notThat}: ${p.label}`} onClick={() => void decide(p.id, "decline")}>
                          {words.notThat}
                        </button>
                      </div>
                    ) : (
                      <p role="status">{words.proposalState[p.state]}</p>
                    )}
                  </div>
                ))}
              </li>
            ))}
          </ul>
          {chatSlow && <p class="note" role="status">{words.chatSlow}</p>}
          <label class="field">
            {words.chatLabel}
            <textarea
              ref={chatRef}
              rows={2}
              maxLength={PLOT_CHAT_MAX_LETTERS}
              value={message}
              onInput={(e) => setMessage((e.target as HTMLTextAreaElement).value)}
            />
          </label>
          {chatProblem && (
            <p class="note bad" role="alert">
              {chatProblem}
            </p>
          )}
          <div class="row">
            <button type="button" class="button" disabled={sending || message.trim() === ""} onClick={() => void send()}>
              {sending ? words.chatSending : words.chatSend}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => {
                onTalk(false);
                keepFocus(() => document.getElementById(titleId));
              }}
            >
              {words.chatClose}
            </button>
          </div>
        </div>
      )}
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}
