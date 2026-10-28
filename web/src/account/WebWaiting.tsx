/** @jsxImportSource preact */
// What waits for the DM on the Story graph (#2635, #2652): facts PlotBot heard at the table, connections
// found in a backstory (grouped by upload, with Keep all), "Which Mara?" questions and "Who is she?" pronoun
// questions (#2831). The DM decides
// everything: each answer is sent to DMbot, which makes the very calls the DM screen's buttons make, so
// consent, secrets and Undo hold the same. Only a DM of the campaign gets the list from the API; a player
// never sees this part. One answer at a time per item: its buttons wait while it is sent, and the list
// is read again after, so what shows is always the API's own.
import { useEffect, useRef, useState } from "preact/hooks";

import { waitingWords as w } from "../content/web-waiting";
import { ApiError, type AccountApi, type Waiting, type WaitingAnswer } from "./api";
import WorkProgress from "./WorkProgress";

/** A session's day for a person ("17 October"), from Unix seconds, the same in every time zone. */
const sessionDay = (seconds: number): string =>
  new Date(seconds * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });

/** Capitalises a line that opens a sentence, so "she grew up with Rook" reads "She grew up with Rook". */
const startCap = (line: string): string => line.charAt(0).toUpperCase() + line.slice(1);

const KEEP_ALL_MAX = 50; // the API keeps at most this many named in one Keep all

type Said = { ok: boolean; text: string; n: number } | null;

const errorWords = (error: unknown): string => {
  if (error instanceof ApiError) return w.errors[error.kind] ?? w.errors.other!;
  return w.errors.other!;
};

export default function WebWaiting({
  api,
  campaignRef,
  onChanged,
}: {
  api: AccountApi;
  campaignRef: string;
  /** Called after an answer was applied, so the page can read the graph again. */
  onChanged?: () => void;
}) {
  const [list, setList] = useState<Waiting | null>(null);
  const [failed, setFailed] = useState(false);
  const [sending, setSending] = useState<{ since: number; stage: string } | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const shown = useRef(campaignRef);
  const [said, setSaid] = useState<Said>(null);
  const counter = useRef(0);
  const latest = useRef(0);
  // A second tap before the page redraws must not send a second answer.
  const inFlight = useRef(false);

  async function read(): Promise<void> {
    if (!api.webWaiting) return;
    const mine = ++latest.current;
    const asked = campaignRef;
    try {
      const got = await api.webWaiting(asked);
      // (another campaign's list is never drawn here: the page may have moved on while this was asked)
      if (mine === latest.current && shown.current === asked) {
        setList(got);
        setFailed(false);
      }
    } catch (error) {
      // A player (or another DM) gets "not found": the part is simply not there.
      if (mine !== latest.current) return;
      if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "not-allowed")) setList(null);
      else setFailed(true);
    }
  }

  useEffect(() => {
    shown.current = campaignRef;
    setList(null);
    setSaid(null);
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function send(
    answer: WaitingAnswer,
    ok: string | ((kept?: number) => string),
    stage: string = w.savingStage,
  ): Promise<void> {
    if (!api.answerWaiting || inFlight.current) return;
    inFlight.current = true;
    setSending({ since: Math.floor(Date.now() / 1000), stage });
    setSaid(null);
    try {
      const got = await api.answerWaiting(campaignRef, answer);
      setSaid({ ok: true, text: typeof ok === "function" ? ok(got.kept) : ok, n: ++counter.current });
      onChanged?.();
    } catch (error) {
      setSaid({ ok: false, text: errorWords(error), n: ++counter.current });
    } finally {
      // The buttons wait until the list has been read again, so nobody answers an item that is already gone.
      await read(); // (what shows is always the API's own, whatever happened)
      inFlight.current = false;
      setSending(null);
      headingRef.current?.focus(); // (the answered item has gone, and focus with it)
    }
  }

  if (!api.webWaiting) return null;
  if (failed && !list)
    return (
      <section class="stack" aria-labelledby="waiting-heading">
        <h3 id="waiting-heading">{w.heading}</h3>
        <p class="warn" role="alert">
          {w.loadFailed}
        </p>
        <button type="button" class="button secondary" onClick={() => void read()}>
          {w.reload}
        </button>
      </section>
    );
  if (!list) return null;

  const busy = sending !== null;
  const { counts } = list;
  const cut = list.claims.length < counts.claims || list.backstory.length < counts.backstory;
  // A pronoun question can have one name or none, and its "Someone else" keeps the connection with a person
  // who isn't named yet, so it gets its own group and words; a name question keeps the "Neither" words.
  const names = list.which.filter((q) => !q.pronoun);
  const pronouns = list.which.filter((q) => q.pronoun);

  // The backstory connections by upload, in the order the API sent them.
  const uploads: { label: string; items: Waiting["backstory"] }[] = [];
  for (const b of list.backstory) {
    const at = uploads.find((u) => u.label === b.upload);
    if (at) at.items.push(b);
    else uploads.push({ label: b.upload, items: [b] });
  }

  return (
    <section class="stack web-waiting" aria-labelledby="waiting-heading">
      <h3 id="waiting-heading" tabIndex={-1} ref={headingRef}>
        {w.heading}
      </h3>
      <p>
        <strong>{counts.all === 0 ? w.none : w.count(counts.all)}</strong>
      </p>
      {counts.all > 0 && <p class="small">{w.intro}</p>}
      {cut && <p class="small">{w.more}</p>}

      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? <span key={said.n}>{said.text}</span> : ""}
      </p>
      {sending && (
        <WorkProgress
          stage={sending.stage}
          startedAt={sending.since}
          serverNow={sending.since}
          state="running"
        />
      )}

      {names.length > 0 && (
        <div class="stack">
          <h4>{w.whichHeading}</h4>
          <p class="small">{w.whichNote}</p>
          {names.map((q) => (
            <div key={q.id} class="panel">
              <p>
                <strong>{w.whichQuestion(q.word)}</strong>
              </p>
              <p id={`wq-${q.id}`}>{w.whichLine(q.other, q.how, q.word, q.otherIsSubject)}</p>
              {q.quote && <p class="muted small">“{q.quote}”</p>}
              {q.secret && <p class="small">{w.secretNote}</p>}
              {q.candidates.some((c) => c.limited) && <p class="small">{w.hiddenNote}</p>}
              <div class="row">
                {q.candidates.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    class="button"
                    disabled={busy}
                    aria-describedby={`wq-${q.id}`}
                    onClick={() => void send({ kind: "which", action: "pick", id: q.id, entity: c.id }, w.done.pick)}
                  >
                    {c.limited ? (
                      <>
                        <span aria-hidden="true">{w.hiddenMark} </span>
                        <span class="visually-hidden">{w.hiddenWord} </span>
                        {c.name}
                      </>
                    ) : (
                      c.name
                    )}
                  </button>
                ))}
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  aria-describedby={`wq-${q.id}`}
                  onClick={() => void send({ kind: "which", action: "neither", id: q.id }, w.done.neither)}
                >
                  {w.neither}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {pronouns.length > 0 && (
        <div class="stack">
          <h4>{w.pronounHeading}</h4>
          <p class="small">{w.pronounNote}</p>
          {pronouns.map((q) => (
            <div key={q.id} class="panel">
              <p>
                <strong>{w.pronounQuestion(q.word)}</strong>
              </p>
              <p id={`wq-${q.id}`}>{startCap(w.whichLine(q.other, q.how, q.word, q.otherIsSubject))}</p>
              {q.quote && <p class="muted small">“{q.quote}”</p>}
              {q.secret && <p class="small">{w.secretNote}</p>}
              {q.candidates.some((c) => c.limited) && <p class="small">{w.hiddenNote}</p>}
              {q.candidates.length === 0 && (
                <p class="small" id={`wq-none-${q.id}`}>
                  {w.pronounNoName}
                </p>
              )}
              <div class="row">
                {q.candidates.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    class="button"
                    disabled={busy}
                    aria-describedby={`wq-${q.id}`}
                    onClick={() => void send({ kind: "which", action: "pick", id: q.id, entity: c.id }, w.done.pick)}
                  >
                    {c.limited ? (
                      <>
                        <span aria-hidden="true">{w.hiddenMark} </span>
                        <span class="visually-hidden">{w.hiddenWord} </span>
                        {c.name}
                      </>
                    ) : (
                      c.name
                    )}
                  </button>
                ))}
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  aria-label={`${w.someoneElse} for ${q.word}`}
                  aria-describedby={q.candidates.length === 0 ? `wq-${q.id} wq-none-${q.id}` : `wq-${q.id}`}
                  onClick={() => void send({ kind: "which", action: "neither", id: q.id }, w.done.someoneElse)}
                >
                  {w.someoneElse}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {list.claims.length > 0 && (
        <div class="stack">
          <h4>{w.claimsHeading}</h4>
          <p class="small">{w.claimsNote}</p>
          {list.claims.map((c) => (
            <div key={c.id} class="panel">
              <p id={`wc-${c.id}`}>{c.statement}</p>
              {typeof c.at === "number" && c.at > 0 && <p class="muted small">{w.saidAt(sessionDay(c.at))}</p>}
              <div class="row">
                <button
                  type="button"
                  class="button"
                  disabled={busy}
                  aria-describedby={`wc-${c.id}`}
                  onClick={() => void send({ kind: "claim", action: "confirm", id: c.id }, w.done.confirm)}
                >
                  {w.confirm}
                </button>
                {c.canSecret && (
                  <button
                    type="button"
                    class="button secondary"
                    disabled={busy}
                    aria-describedby={`wc-${c.id}`}
                    onClick={() =>
                      void send({ kind: "claim", action: "confirm", id: c.id, secret: true }, w.done.confirmSecret)
                    }
                  >
                    {w.confirmSecret}
                  </button>
                )}
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  aria-describedby={`wc-${c.id}`}
                  onClick={() => void send({ kind: "claim", action: "reject", id: c.id }, w.done.reject)}
                >
                  {w.reject}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {list.backstory.length > 0 && (
        <div class="stack">
          <h4>{w.backstoryHeading}</h4>
          <p class="small">{w.backstoryNote}</p>
          {uploads.map((u) => {
            const ready = u.items.filter((b) => b.ready);
            const ids = ready.slice(0, KEEP_ALL_MAX).map((b) => b.id);
            const left = ready.length - ids.length;
            return (
              <div key={u.label} class="stack">
                <h5>{u.label ? w.fromUpload(u.label) : w.fromEarlier}</h5>
                {ready.length > 1 && (
                  <div class="row">
                    <button
                      type="button"
                      class="button"
                      disabled={busy}
                      onClick={() =>
                        void send({ kind: "backstory", action: "keep_all", ids },
                          (kept) => (left > 0 && kept !== undefined ? w.keepAllCut(kept, left) : w.kept(kept)),
                          w.keepingStage(ids.length),
                        )
                      }
                    >
                      {w.keepAll(ids.length)}
                    </button>
                  </div>
                )}
                {u.items.map((b) => (
                  <div key={b.id} class="panel">
                    <p id={`wb-${b.id}`}>{`${b.subject} ${b.how} ${b.object}`}</p>
                    {b.quote && <p class="muted small">“{b.quote}”</p>}
                    {b.secret && <p class="small">{w.secretNote}</p>}
                    {b.ready ? (
                      <div class="row">
                        <button
                          type="button"
                          class="button"
                          disabled={busy}
                          aria-describedby={`wb-${b.id}`}
                          onClick={() => void send({ kind: "backstory", action: "keep", id: b.id }, w.done.keep)}
                        >
                          {b.secret ? w.keepIsSecret : w.keep}
                        </button>
                        {!b.secret && (
                          <button
                            type="button"
                            class="button secondary"
                            disabled={busy}
                            aria-describedby={`wb-${b.id}`}
                            onClick={() =>
                              void send({ kind: "backstory", action: "keep", id: b.id, secret: true }, w.done.keepSecret)
                            }
                          >
                            {w.keepSecret}
                          </button>
                        )}
                        <button
                          type="button"
                          class="button secondary"
                          disabled={busy}
                          aria-describedby={`wb-${b.id}`}
                          onClick={() => void send({ kind: "backstory", action: "skip", id: b.id }, w.done.skip)}
                        >
                          {w.skip}
                        </button>
                      </div>
                    ) : (
                      <>
                        <p class="small">{w.waitingOn(b.waitingOn)}</p>
                        <div class="row">
                          <button
                            type="button"
                            class="button secondary"
                            disabled={busy}
                            aria-describedby={`wb-${b.id}`}
                            onClick={() => void send({ kind: "backstory", action: "skip", id: b.id }, w.done.skip)}
                          >
                            {w.skip}
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
