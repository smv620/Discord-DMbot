/** @jsxImportSource preact */
// "Tell DMbot what to fix" on a recap's page (#2114; core #2113). The DM types a note; the web API only
// queues it, the bot sorts it into points, and this page polls for them (every 2 seconds, about a
// minute at most). The DM then removes corrections they don't want and chooses which style notes to
// keep, and presses Rewrite: that uses one of the recap's tries and clears the approval. DMbot only
// sorts and rewrites; the DM decides. Replies come back in plain words and are shown as they are.
// Shown only while RECAPS_LIVE and RECAP_FIX_LIVE are true, for a recap that can still change.
import { useEffect, useRef, useState } from "preact/hooks";

import { FIX_MAX_LETTERS, fixWords as words } from "../content/recap-fix";
import { RECAP_FIX_LIVE, RECAPS_LIVE } from "../content/recap-words";
import { ApiError, type AccountApi, type FixPoints, type Recap } from "./api";
import { keepFocus } from "./focus";

const POLL_MS = 2000;
const POLL_LIMIT = 30; // about a minute

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
  const [corrections, setCorrections] = useState<Set<string>>(new Set());
  const [style, setStyle] = useState<Set<string>>(new Set());
  const [sorting, setSorting] = useState(false);
  const [slow, setSlow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const [triesLeft, setTriesLeft] = useState<number | null>(null);
  const inFlight = useRef(false);
  const statusRef = useRef<HTMLParagraphElement>(null);

  const fail = (error: unknown): void => {
    const kind = error instanceof ApiError ? error.kind : "server";
    setProblem(words.problem[kind] ?? words.problem.server);
  };

  // Follow the request until the bot has sorted the note, or about a minute has passed.
  useEffect(() => {
    if (!requestId || !api.fixResult) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    const tick = async (): Promise<void> => {
      try {
        const got = await api.fixResult!(recap.ref, requestId);
        if (!live) return;
        if (got.state === "done" && got.points) {
          setPoints(got.points);
          setCorrections(new Set(got.points.corrections.map((c) => c.id))); // kept until removed
          setStyle(new Set()); // a style note shapes later recaps, so it is only kept on purpose
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
    setPoints(null);
    setStarted(false);
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

  const flip = (set: Set<string>, id: string, on: boolean): Set<string> => {
    const next = new Set(set);
    if (on) next.add(id);
    else next.delete(id);
    return next;
  };

  const keptCount = corrections.size + style.size;

  const rewrite = async (): Promise<void> => {
    if (inFlight.current || !api.rewriteWith || !requestId || keptCount === 0)
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
      setTriesLeft(done.triesLeft);
      onRecap(done.recap);
      setPoints(null);
      setText("");
      setStarted(true);
      keepFocus(() => statusRef.current);
    } catch (error) {
      fail(error);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

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
        {sorting
          ? words.sorting
          : slow
            ? words.slow
            : started
              ? words.started
              : ""}
      </p>
      <label class="field">
        {words.label}
        <textarea
          rows={4}
          maxLength={FIX_MAX_LETTERS}
          value={text}
          onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
        />
      </label>
      <p class="muted small">{words.count(text.length)}</p>
      <button
        type="button"
        class="button"
        disabled={busy || sorting || text.trim() === ""}
        onClick={() => void send()}
      >
        {sorting ? words.sorting : words.send}
      </button>

      {points && (
        <div>
          {points.corrections.length > 0 && (
            <>
              <h4>{words.correctionsHeading}</h4>
              <ul class="campaigns">
                {points.corrections.map((c) => (
                  <li key={c.id}>
                    <span class="campaign-name">{c.text}</span>
                    <span class="small">
                      {corrections.has(c.id) ? words.kept : words.notKept}
                    </span>
                    {corrections.has(c.id) && (
                      <button
                        type="button"
                        class="button secondary"
                        aria-label={words.removeOne(c.text)}
                        onClick={() =>
                          setCorrections((now) => flip(now, c.id, false))
                        }
                      >
                        {words.remove}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {points.style.length > 0 && (
            <>
              <h4>{words.styleHeading}</h4>
              <p class="muted small">{words.styleLead}</p>
              <ul class="campaigns">
                {points.style.map((s) => (
                  <li key={s.id}>
                    <span class="campaign-name">{s.text}</span>
                    <span class="small">
                      {style.has(s.id) ? words.kept : words.notKept}
                    </span>
                    <button
                      type="button"
                      class="button secondary"
                      aria-label={
                        style.has(s.id)
                          ? words.dontKeepOne(s.text)
                          : words.keepOne(s.text)
                      }
                      onClick={() =>
                        setStyle((now) => flip(now, s.id, !now.has(s.id)))
                      }
                    >
                      {style.has(s.id) ? words.dontKeep : words.keep}
                    </button>
                  </li>
                ))}
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
              <p class="small">
                {recap.triesLeft > 0
                  ? words.cost(recap.triesLeft)
                  : words.costNoCount}
              </p>
              {keptCount === 0 && <p class="small">{words.nothingKept}</p>}
              <button
                type="button"
                class="button"
                disabled={busy || keptCount === 0}
                onClick={() => void rewrite()}
              >
                {busy ? words.rewriting : words.rewrite}
              </button>
            </>
          )}
        </div>
      )}
      {triesLeft !== null && started && (
        <p class="muted small">{words.cost(triesLeft)}</p>
      )}
    </section>
  );
}
