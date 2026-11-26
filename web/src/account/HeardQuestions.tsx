/** @jsxImportSource preact */
// The "Did they mean…?" questions on the Questions page (#4841 B1, the page half of #4857): the DM names the word as heard, keeps
// it as heard, or puts it off with ⏳ Later, one question at a time, in Discord's words. Its own file, loaded only from the
// Questions page while HEARD_LIVE. DM only: the door answers anyone else with nothing. "Type it…" (#4865) names a word the DM types.
import { useEffect, useRef, useState } from "preact/hooks";

import { heardWords as w } from "../content/heard-words";
import { ApiError, type AccountApi, type HeardOption, type HeardQuestion } from "./api";
import SavingWait from "./SavingWait";

/** The plain line for an answer that did not go through, by the bot's error word. */
function sayFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "expired") return w.expired;
  if (error.kind === "gone") return w.gone;
  if (error.kind === "only-dm") return w.onlyDm;
  if (error.kind === "name-gone") return w.nameGone;
  if (error.kind === "cant-check") return w.cantCheck;
  if (error.kind === "not-saved") return w.notSaved;
  return w.couldNot;
}

/** The line for a typed name the bot refused: its reason's words, or the check-again words for a reason it has none for. */
function typedLine(error: ApiError): string {
  if (error.kind === "typed-refused") return w.typedRefused;
  const reason = error.reason;
  return reason !== undefined && Object.hasOwn(w.typedProblem, reason) ? (w.typedProblem[reason] ?? w.cantCheck) : w.cantCheck;
}

/** The line for a saved answer, by the code the bot saved (`kept`, `fixed`, `fixed-new` or `line-kept`). */
function savedLine(code: string | null, heard: string, name: string): string {
  if (code === "kept") return w.kept(heard);
  if (code === "fixed") return w.fixed(heard, name);
  if (code === "fixed-new") return `${w.fixed(heard, name)} ${w.newName(name)}`;
  if (code === "line-kept") return w.lineKept(heard, name);
  return w.gotIt;
}

export default function HeardQuestions({
  api,
  campaignRef,
  id,
  onLeft,
}: {
  api: AccountApi;
  campaignRef: string;
  id: string;
  onLeft?: (left: number) => void;
}) {
  const [questions, setQuestions] = useState<HeardQuestion[] | null>(null);
  const [failed, setFailed] = useState(false);
  // The questions put off with ⏳ Later: they are hidden until the list is read again.
  const [later, setLater] = useState<Set<string>>(() => new Set());
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // "Type it…" (#4865): the question whose form is open, the name in its box, and the words for a name the bot refused.
  const [typing, setTyping] = useState<{ question: string } | null>(null);
  const [typed, setTyped] = useState("");
  const [typeProblem, setTypeProblem] = useState<string | null>(null);
  const flight = useRef(false);
  // Focus follows the form (#4865): the box when it opens or a name is refused; the line that says what happened after a save or a close.
  const box = useRef<HTMLInputElement>(null);
  const typeButton = useRef<HTMLButtonElement>(null);
  const okLine = useRef<HTMLParagraphElement>(null);
  const alertLine = useRef<HTMLParagraphElement>(null);
  const [focusBox, setFocusBox] = useState(0);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.heardQuestions) return;
    try {
      const got = await api.heardQuestions(campaignRef);
      if (!live.current) return;
      setQuestions(got.questions);
      setFailed(false);
      setLater(new Set());
      onLeft?.(got.questions.length);
    } catch {
      if (live.current) setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);
  useEffect(() => {
    if (typing) box.current?.focus();
  }, [typing, focusBox]);
  useEffect(() => {
    if (said) (said.ok ? okLine : alertLine).current?.focus();
  }, [said]);
  // A question that has gone from the list takes its form with it (#4865).
  useEffect(() => {
    if (typing && questions && !questions.some((x) => x.id === typing.question)) {
      setTyping(null);
      setTyped("");
      setTypeProblem(null);
    }
  }, [questions, typing]);

  // One answer in flight at a time; the list is read again after each one, so what shows is always the API's own.
  async function press(run: () => Promise<void>): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await run();
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error) });
    } finally {
      await read();
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  const pick = (q: HeardQuestion, opt: HeardOption) =>
    press(async () => {
      const got = await api.heardPick!(campaignRef, q.id, opt.index);
      if (live.current) setSaid({ ok: true, text: savedLine(got.code, q.heard, opt.name) });
    });
  const keep = (q: HeardQuestion) =>
    press(async () => {
      const got = await api.heardKeep!(campaignRef, q.id);
      if (live.current) setSaid({ ok: true, text: savedLine(got.code, q.heard, q.heard) });
    });

  // A typed name (#4865): a problem keeps the question and the form open, with what was typed still in the box. A blank box is
  // said on the page without a round trip; the name sent is trimmed.
  async function saveTyped(q: HeardQuestion): Promise<void> {
    if (flight.current || !api.heardType) return;
    const name = typed.trim();
    setTypeProblem(null);
    if (name === "") {
      setTypeProblem(w.typedProblem.empty ?? w.cantCheck);
      setFocusBox((was) => was + 1);
      return;
    }
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      const got = await api.heardType(campaignRef, q.id, name);
      if (live.current) {
        setTyping(null);
        setTyped("");
        setSaid({ ok: true, text: savedLine(got.code, q.heard, name) });
      }
    } catch (error) {
      if (live.current && error instanceof ApiError && (error.kind === "typed-problem" || error.kind === "typed-refused")) {
        setTypeProblem(typedLine(error));
        setFocusBox((was) => was + 1);
      } else {
        // An expired or closed question, or one that can't be answered by this person, closes its form.
        if (live.current && error instanceof ApiError && (error.kind === "expired" || error.kind === "gone" || error.kind === "only-dm")) {
          setTyping(null);
          setTyped("");
        }
        if (live.current) setSaid({ ok: false, text: sayFailure(error) });
      }
    } finally {
      await read();
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  if (!api.heardQuestions) return null;
  if (failed && !questions) {
    return (
      <div id={id} class="stack">
        <p class="warn" role="alert">{w.failed}</p>
      </div>
    );
  }
  if (!questions) return <p class="muted" role="status">{w.loading}</p>;

  const shown = questions.findIndex((q) => !later.has(q.id));
  const q = shown >= 0 ? questions[shown] : undefined;
  const more = q ? questions.length - shown - 1 : 0;

  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>{w.title}</h3>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status" ref={okLine} tabIndex={-1}>{said?.ok ? said.text : ""}</p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert" ref={alertLine} tabIndex={-1}>{said && !said.ok ? said.text : ""}</p>
      {q ? (
        <>
          <p>
            <strong>{w.question(q.speaker, q.heard)}</strong>
          </p>
          <p class="small">{w.notSure}</p>
          {more > 0 && <p class="small muted">{w.moreAfter(more)}</p>}
          <div class="row">
            {q.options.map((opt) => (
              <button key={opt.id} type="button" class="button secondary" disabled={busy} onClick={() => void pick(q, opt)}>
                {w.optionLabel(opt.name)}
              </button>
            ))}
          </div>
          <div class="row">
            <button type="button" class="button secondary" disabled={busy} onClick={() => void keep(q)}>
              {w.keep(q.heard)}
            </button>
            <button
              type="button"
              class="button secondary"
              ref={typeButton}
              disabled={busy || !api.heardType}
              onClick={() => {
                setTyping({ question: q.id });
                setTyped("");
                setTypeProblem(null);
              }}
            >
              {w.typeIt}
            </button>
            <button type="button" class="button secondary" disabled={busy} onClick={() => setLater((was) => new Set(was).add(q.id))}>
              {w.later}
            </button>
          </div>
          {typing?.question === q.id && (
            <form
              class="stack"
              aria-label={w.typeTheName}
              onSubmit={(e) => {
                e.preventDefault();
                void saveTyped(q);
              }}
            >
              <h4>{w.typeTheName}</h4>
              <label>
                {w.typeLabel}
                <input
                  ref={box}
                  type="text"
                  maxLength={60}
                  value={typed}
                  readOnly={busy}
                  aria-busy={busy}
                  aria-describedby={`${id}-hint`}
                  onInput={(e) => setTyped((e.target as HTMLInputElement).value)}
                />
              </label>
              <p class="small muted" id={`${id}-hint`}>
                {w.typeHint}
              </p>
              {typeProblem && (
                <p class="warn" role="alert">
                  {typeProblem}
                </p>
              )}
              <div class="row">
                <button type="submit" class="button" disabled={busy}>
                  {w.save}
                </button>
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  onClick={() => {
                    setTyping(null);
                    setTyped("");
                    setTypeProblem(null);
                    typeButton.current?.focus();
                  }}
                >
                  {w.cancel}
                </button>
              </div>
            </form>
          )}
          <SavingWait active={busy} stage={w.saving} />
        </>
      ) : (
        <p class="small">{questions.length === 0 ? w.allChecked : w.allLater}</p>
      )}
    </section>
  );
}
