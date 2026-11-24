/** @jsxImportSource preact */
// "Heads-ups from this session" on the Names page (#3695, page half 1): where the story may clash with itself, answered one at a time
// in Discord's words, five on the page at once. DM only: the door answers anyone else with not-allowed. Nothing is asked or read while
// CONTINUITY_LIVE is off; a second press waits for the first answer; no name, quote or word of the story goes into the title or a URL.
import { Fragment } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { CONTINUITY_LIVE } from "../content/continuity-switch";
import { continuityWords as w } from "../content/continuity-words";
import { ApiError, type AccountApi, type ContinuityHit } from "./api";
import SavingWait from "./SavingWait";

const SHOWN = 5;
type Said = { ok: boolean; text: string };

/** Text and bold runs for the parts that are there: an empty part is left out, with its space. */
function joined(parts: [string, boolean][]) {
  const shown = parts.filter(([text]) => text !== "");
  return shown.map(([text, bold], i) => (
    <Fragment key={i}>
      {i > 0 ? " " : ""}
      {bold ? <strong>{text}</strong> : text}
    </Fragment>
  ));
}

/** "At the table: subject relationship object (details)." */
function nowLine(h: ContinuityHit) {
  const { subject, relationship, object, details } = h.now;
  return (
    <>
      At the table: {joined([[subject, true], [relationship, false], [object, true]])}
      {details ? ` (${details})` : ""}.
    </>
  );
}

/** One heads-up, as Discord's card says it. */
function lineOf(h: ContinuityHit) {
  const level = w.levelOf(h.level);
  const when = h.earlierSession != null ? w.inSession(h.earlierSession) : "";
  const quote = h.quote != null ? w.quoted(h.quote) : "";
  const before = h.earlierWords || w.before;
  if (h.kind === "knows_a_secret") {
    const listed = h.listed && h.listed.length > 0 ? h.listed.join(", ") : w.emptyList;
    return (
      <>
        ⚠️ Check: <strong>{h.learner || w.unknownLearner}</strong> spoke of <strong>{h.name}</strong> and isn't on its list ({listed}). {nowLine(h)} ({h.level.toLowerCase()})
      </>
    );
  }
  if (h.ended != null) {
    return (
      <>
        ⚠️ {level}: <strong>{h.name}</strong> {h.ended === "destroyed" ? w.destroyed : w.died}
        {when}
        {quote}. {nowLine(h)}
      </>
    );
  }
  if (h.pinned) {
    return (
      <>
        📌 Check: <strong>{h.name}</strong>. You pinned: {before}
        {quote}. {nowLine(h)} ({h.level.toLowerCase()})
      </>
    );
  }
  return (
    <>
      ⚠️ {level}: <strong>{h.name}</strong>. Before{when}: {before}
      {quote}. {nowLine(h)}
    </>
  );
}

/** The first button says what the heads-up's own choice is; its answer is always "Story changed". */
function firstLabel(choice: ContinuityHit["choice"]): string {
  if (choice === "they_learned_it") return w.theyLearned;
  if (choice === "still_here") return w.stillHere;
  if (choice === "alive_after_all") return w.aliveAfterAll;
  return w.storyChanged;
}

export default function Continuity({ api, refId, onSignedOut }: { api: AccountApi; refId: string; onSignedOut?: () => void }) {
  const [hits, setHits] = useState<{ total: number; list: ContinuityHit[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Lines set aside with Ignore stay off the page, without a call, until the page is reloaded (the panel's state is not kept).
  const [ignored, setIgnored] = useState<Set<string>>(new Set());
  const flight = useRef(false);
  const live = useRef(true);
  // The panel's campaign now: a read that was asked for an earlier campaign must not overwrite this one's list.
  const now = useRef(refId);
  now.current = refId;
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.continuityHits) return;
    const asked = refId;
    try {
      const got = await api.continuityHits(asked);
      if (!live.current || now.current !== asked) return;
      setHits({ total: got.total, list: got.hits });
      setProblem(null);
    } catch (error) {
      if (!live.current || now.current !== asked) return;
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      setProblem(w.failed);
    }
  }

  // The bot looks first, then the list is read: after opening, and after every answer (Discord re-reads the same way).
  async function askAndRead(): Promise<void> {
    if (api.askContinuity) {
      try {
        await api.askContinuity(refId);
      } catch {
        // (the read below still shows what is waiting)
      }
    }
    await read();
  }

  useEffect(() => {
    if (!CONTINUITY_LIVE || !api.continuityHits) return;
    setBusy("ask");
    void askAndRead().finally(() => live.current && setBusy(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, refId]);

  async function answer(h: ContinuityHit, choice: "changed" | "not"): Promise<void> {
    if (flight.current || !api.answerContinuity) return;
    flight.current = true;
    setBusy(h.id);
    setSaid(null);
    try {
      await api.answerContinuity(refId, { claim: h.id, choice });
      if (live.current) setSaid({ ok: true, text: choice === "not" ? w.wasnt_done : h.choice === "they_learned_it" ? w.learned_done : w.changed_done });
    } catch (error) {
      if (live.current) {
        if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
        const kind = error instanceof ApiError ? error.kind : "";
        setSaid({ ok: false, text: kind === "gone" ? w.gone : kind === "refused" ? w.refused : w.couldNot });
      }
    } finally {
      if (live.current) await askAndRead(); // (what shows is always the API's own; nothing after a sign-out or an unmount)
      flight.current = false;
      if (live.current) setBusy(null);
    }
  }

  if (!CONTINUITY_LIVE || !api.continuityHits || !api.answerContinuity) return null;
  if (problem && !hits) {
    return (
      <div class="stack">
        <p class="warn" role="alert">{problem}</p>
      </div>
    );
  }
  if (!hits) return <p class="muted" role="status">{w.loading}</p>;

  const visible = hits.list.filter((h) => !ignored.has(h.id)).slice(0, SHOWN);
  const more = hits.total - visible.length;
  const pinned = visible.some((h) => h.pinned);

  return (
    <section class="stack" aria-labelledby="continuity-heading">
      <h3 id="continuity-heading">{w.heading}</h3>
      <p>{w.lead}</p>
      <p class="small">
        {w.explain}
        {pinned ? w.pinNote : ""}
      </p>
      {said && (
        <p role={said.ok ? "status" : "alert"} class={said.ok ? "ok" : "warn"}>
          {said.text}
        </p>
      )}
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      {hits.total === 0 && <p role="status">{w.nothingLeft}</p>}
      {visible.length > 0 && (
        <ol class="stack">
          {visible.map((h) => (
            <li key={h.id} class="stack">
              <p>{lineOf(h)}</p>
              <div class="row">
                <button type="button" class="button" disabled={busy !== null} onClick={() => void answer(h, "changed")}>
                  {firstLabel(h.choice)}
                </button>
                <button type="button" class="button secondary" disabled={busy !== null} onClick={() => void answer(h, "not")}>
                  {w.wasnt}
                </button>
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy !== null}
                  onClick={() => setIgnored((was) => new Set(was).add(h.id))}
                >
                  {w.ignore}
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}
      {more > 0 && <p class="small muted">{w.more(more)}</p>}
      <SavingWait active={busy !== null} stage={w.stage} />
    </section>
  );
}
