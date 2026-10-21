/** @jsxImportSource preact */
// "Tell DMbot what to fix" on a recap's page (#2114; core #2113). The DM types a note; the web API only
// queues it, the bot sorts it into points, and this page polls for them (every 2 seconds, about a
// minute at most). The DM then removes corrections they don't want and chooses which style notes to
// keep, and presses Rewrite: that uses one Try again and clears the approval. DMbot only sorts and
// rewrites; the DM decides. Replies come back in plain words and are shown as they are.
// Shown only while RECAPS_LIVE and RECAP_FIX_LIVE are true, for a recap that can still change.
// The page is keyed by the recap (Recaps.tsx), so nothing carries over from one recap to another.
import { useEffect, useRef, useState } from "preact/hooks";

import { FIX_MAX_LETTERS, fixWords as words } from "../content/recap-fix";
import { RECAP_FIX_LIVE, RECAP_VOICE_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type FixPoints, type Recap } from "./api";
import { keepFocus } from "./focus";
import RecapVoice from "./RecapVoice";
import { voiceWords } from "../content/recap-voice";

const POLL_MS = 2000;
const POLL_LIMIT = 60; // about two minutes: a sort can take up to 90 seconds

const flip = (set: Set<string>, id: string, on: boolean): Set<string> => {
  const next = new Set(set);
  if (on) next.add(id);
  else next.delete(id);
  return next;
};

export default function RecapFix({
  api,
  recap,
  onRecap,
}: {
  api: AccountApi;
  recap: Recap;
  onRecap: (next: Recap) => void;
}) {
  const [text, setText] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [points, setPoints] = useState<FixPoints | null>(null);
  // Corrections start kept and are removed on purpose; a style note starts not kept and is kept on purpose.
  const [corrections, setCorrections] = useState<Set<string>>(new Set());
  const [style, setStyle] = useState<Set<string>>(new Set());
  const [sorting, setSorting] = useState(false);
  const [slow, setSlow] = useState(false);
  const [checks, setChecks] = useState(0); // "Check again" on the same request after the page gave up
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [started, setStarted] = useState<"approve" | "plain" | null>(null);
  const inFlight = useRef(false);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const [cutOff, setCutOff] = useState(false);
  // The words in the box as they are now: a spoken note that comes back later is added after whatever
  // was typed while it was being turned into words.
  const textRef = useRef("");
  textRef.current = text;

  const fail = (error: unknown): string => {
    const kind = error instanceof ApiError ? error.kind : "server";
    const message = words.problem[kind] ?? words.problem.server;
    setProblem(message);
    return kind;
  };

  // Follow the request until the bot has sorted the note, or about a minute has passed.
  useEffect(() => {
    const read = api.fixResult;
    if (!requestId || !read) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    let retried = false;
    const tick = async (): Promise<void> => {
      try {
        const got = await read(recap.ref, requestId);
        if (!live) return;
        if (got.state === "done") {
          if (!got.points) throw new ApiError("server"); // "done" with nothing to show is a fault
          setPoints(got.points);
          setCorrections(new Set(got.points.corrections.map((c) => c.id)));
          setStyle(new Set());
          setSorting(false);
          return;
        }
        if (got.state === "failed") {
          setSorting(false);
          // The bot says why: recaps off for this campaign, the campaign's note limit, or a plain failure.
          const kind =
            got.error === "not_allowed"
              ? "not-allowed"
              : got.error === "too_soon"
                ? "too-soon"
                : "sort-failed";
          setProblem(words.problem[kind] ?? words.problem.server);
          return;
        }
        polls += 1;
        if (polls >= POLL_LIMIT) {
          setSorting(false);
          setSlow(true);
          return;
        }
        timer = setTimeout(() => void tick(), POLL_MS);
      } catch (error) {
        if (!live) return;
        // One dropped connection is not the end of a sort that is still running: look once more.
        if (error instanceof ApiError && error.kind === "network" && !retried) {
          retried = true;
          timer = setTimeout(() => void tick(), POLL_MS);
          return;
        }
        setSorting(false);
        fail(error);
      }
    };
    void tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [api, recap.ref, requestId, checks]);

  // The "being written again" line goes when the new draft is ready and can be changed again.
  useEffect(() => {
    // Also when nothing is being written and it still can't be changed: a failed rewrite must not
    // leave "writing again" on screen.
    if (started && (recap.canEdit || !recap.working)) setStarted(null);
  }, [started, recap.canEdit, recap.working]);

  // Once a rewrite has started the recap cannot be changed for a while (it is being written), but
  // the line saying so stays.
  if (
    !RECAPS_LIVE ||
    !RECAP_FIX_LIVE ||
    !api.sendFix ||
    (!recap.canEdit && !started)
  )
    return null;

  const send = async (): Promise<void> => {
    if (inFlight.current || !api.sendFix || text.trim() === "") return;
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    setSlow(false);
    // A new note starts clean: nothing from an earlier request may be sent with this one.
    setRequestId(null);
    setPoints(null);
    setCorrections(new Set());
    setStyle(new Set());
    setStarted(null);
    setCutOff(false);
    try {
      const { requestId: id } = await api.sendFix(recap.ref, text.trim());
      setSorting(true);
      setRequestId(id);
    } catch (error) {
      fail(error);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const keptCount = corrections.size + style.size;
  const noTries = recap.triesLeft <= 0;

  const rewrite = async (): Promise<void> => {
    if (
      inFlight.current ||
      !api.rewriteWith ||
      !requestId ||
      keptCount === 0 ||
      noTries
    )
      return;
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      const done = await api.rewriteWith(
        recap.ref,
        requestId,
        [...corrections],
        [...style],
      );
      onRecap(done.recap);
      setRequestId(null);
      setPoints(null);
      setText("");
      setStarted(done.needsApproval ? "approve" : "plain");
      keepFocus(() => statusRef.current);
    } catch (error) {
      const kind = fail(error);
      if (kind === "bad-choice" || kind === "not-found") {
        // The points are out of date (or the request was swept): take them away so they can't be
        // sent again, and say to send the note again. A missing recap is found by the page itself.
        setProblem(words.problem["bad-choice"] ?? words.problem.server);
        setRequestId(null);
        setPoints(null);
      } else if (kind === "too-soon") {
        setProblem(words.problem["too-soon-rewrite"] ?? words.problem.server);
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const status = sorting
    ? words.sorting
    : slow
      ? words.slow
      : started === "approve"
        ? words.started
        : started === "plain"
          ? words.startedNoApproval
          : points
            ? points.corrections.length + points.style.length === 0
              ? points.replies.length > 0
                ? "" // the answer below says it; one line is enough
                : words.nothing
              : words.sorted(points.corrections.length + points.style.length)
            : "";

  return (
    <section class="panel" aria-labelledby="recap-fix">
      <h3 id="recap-fix">{words.heading}</h3>
      <p class="muted small">{words.lead}</p>
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
      <p
        role="status"
        aria-live="polite"
        tabIndex={-1}
        ref={statusRef}
        class="small"
      >
        {status}
      </p>
      {cutOff && (
        <p class="note" role="status">
          {voiceWords.cutOff}
        </p>
      )}
      {RECAP_VOICE_LIVE && (
        <RecapVoice
          api={api}
          recap={recap}
          onHeard={(heard) => {
            // The words are only put in the box: nothing is sent until the DM presses Send.
            // Words already typed stay: the heard words are added after them.
            const now = textRef.current;
            const joined = now.trim() === "" ? heard : `${now.trimEnd()} ${heard}`;
            setCutOff(joined.length > FIX_MAX_LETTERS);
            setText(joined.slice(0, FIX_MAX_LETTERS));
            noteRef.current?.focus();
          }}
        />
      )}
      <label class="field">
        {words.label}
        <textarea
          ref={noteRef}
          rows={4}
          maxLength={FIX_MAX_LETTERS}
          value={text}
          disabled={!recap.canEdit}
          aria-describedby="recap-fix-count"
          onInput={(e) => {
            setText((e.target as HTMLTextAreaElement).value);
            setCutOff(false);
          }}
        />
      </label>
      <p class="muted small" id="recap-fix-count">
        {words.count(text.length)}
      </p>
      <button
        type="button"
        class="button"
        disabled={busy || sorting || text.trim() === "" || !recap.canEdit}
        onClick={() => void send()}
      >
        {busy && !sorting ? words.sending : words.send}
      </button>
      {slow && requestId && (
        <button
          type="button"
          class="button secondary"
          onClick={() => {
            // Look again at the same request: the bot may still finish it, and a new Send counts toward the limit.
            setSlow(false);
            setSorting(true);
            setChecks((n) => n + 1);
          }}
        >
          {words.checkAgain}
        </button>
      )}

      {points && (
        <div>
          {points.corrections.length > 0 && (
            <>
              <h4>{words.correctionsHeading}</h4>
              <ul class="campaigns">
                {points.corrections.map((c) => {
                  const on = corrections.has(c.id);
                  return (
                    <li key={c.id}>
                      <span class="campaign-name">{c.text}</span>
                      <span class="small">
                        {on ? words.kept : words.notKept}
                      </span>
                      <button
                        type="button"
                        class="button secondary"
                        aria-label={
                          on
                            ? words.removeOne(c.text)
                            : words.putBackOne(c.text)
                        }
                        onClick={() =>
                          setCorrections((now) => flip(now, c.id, !on))
                        }
                      >
                        {on ? words.remove : words.putBack}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          {points.style.length > 0 && (
            <>
              <h4>{words.styleHeading}</h4>
              <p class="muted small">{words.styleLead}</p>
              <ul class="campaigns">
                {points.style.map((s) => {
                  const on = style.has(s.id);
                  return (
                    <li key={s.id}>
                      <span class="campaign-name">{s.text}</span>
                      <span class="small">
                        {on ? words.kept : words.notKept}
                      </span>
                      <button
                        type="button"
                        class="button secondary"
                        aria-label={
                          on ? words.dontKeepOne(s.text) : words.keepOne(s.text)
                        }
                        onClick={() => setStyle((now) => flip(now, s.id, !on))}
                      >
                        {on ? words.dontKeep : words.keep}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          {points.replies.length > 0 && (
            <>
              <h4>{words.repliesHeading}</h4>
              {points.replies.map((r, i) => (
                <p key={i}>{r}</p>
              ))}
            </>
          )}
          {points.corrections.length + points.style.length > 0 && (
            <>
              <p>{noTries ? words.noTriesLeft : words.cost(recap.triesLeft)}</p>
              {keptCount === 0 && <p class="small">{words.nothingKept}</p>}
              <button
                type="button"
                class="button"
                disabled={busy || keptCount === 0 || noTries}
                onClick={() => void rewrite()}
              >
                {busy ? words.rewriting : words.rewrite}
              </button>
            </>
          )}
        </div>
      )}
    </section>
  );
}
