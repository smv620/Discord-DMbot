/** @jsxImportSource preact */
// The "Did they mean…?" questions on the Questions page (#4841 B1, the page half of #4857): the DM names the word as heard, keeps
// it as heard, or puts it off with ⏳ Later, one question at a time, in Discord's words. Its own file, loaded only from the
// Questions page while HEARD_LIVE. DM only: the door answers anyone else with nothing. "Type it…" is a later slice.
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

/** The line for a saved answer, by the code the bot saved (`kept`, `fixed` or `line-kept`). */
function savedLine(code: string | null, heard: string, name: string): string {
  if (code === "kept") return w.kept(heard);
  if (code === "fixed") return w.fixed(heard, name);
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
  const flight = useRef(false);
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
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">{said?.ok ? said.text : ""}</p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">{said && !said.ok ? said.text : ""}</p>
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
            <button type="button" class="button secondary" disabled={busy} onClick={() => setLater((was) => new Set(was).add(q.id))}>
              {w.later}
            </button>
          </div>
          <SavingWait active={busy} stage={w.saving} />
        </>
      ) : (
        <p class="small">{questions.length === 0 ? w.allChecked : w.allLater}</p>
      )}
    </section>
  );
}
