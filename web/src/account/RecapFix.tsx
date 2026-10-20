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
import { RECAP_FIX_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type FixPoints, type Recap } from "./api";
import { keepFocus } from "./focus";

const POLL_MS = 2000;
const POLL_LIMIT = 30; // about a minute

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
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [started, setStarted] = useState<"approve" | "plain" | null>(null);
  const inFlight = useRef(false);
  const statusRef = useRef<HTMLParagraphElement>(null);

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
          setProblem(
            words.problem[
              got.error === "not_allowed" ? "not-allowed" : "sort-failed"
            ] ?? words.problem.server,
          );
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
        setSorting(false);
        fail(error);
      }
    };
    void tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [api, recap.ref, requestId]);

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
      if (fail(error) === "bad-choice") {
        // The points are out of date: take them away so they can't be sent again.
        setRequestId(null);
        setPoints(null);
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
              ? words.nothing
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
      <label class="field">
        {words.label}
        <textarea
          rows={4}
          maxLength={FIX_MAX_LETTERS}
          value={text}
          disabled={!recap.canEdit}
          aria-describedby="recap-fix-count"
          onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
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
              <p>
                {noTries
                  ? words.noTriesLeft
                  : recap.triesLeft > 0
                    ? words.cost(recap.triesLeft)
                    : words.costNoCount}
              </p>
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
