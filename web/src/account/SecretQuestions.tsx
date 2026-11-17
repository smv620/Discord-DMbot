/** @jsxImportSource preact */
// The 🤫 Known or secret? question on the Questions page (#4598; core web/secret_questions.py): one question at a time, in the
// words the API gives (the same as Discord's card). A DM only: the door answers anyone else with nothing. A keep changes nothing; a
// make changes what the players know, and then Undo takes it back until the next answer. The wait shows its stage, how long and,
// after a minute, that it is slow.
import { useEffect, useRef, useState } from "preact/hooks";

import { secretWords as w } from "../content/secret-questions-words";
import { ApiError, type AccountApi, type SecretQuestion } from "./api";
import SavingWait from "./SavingWait";

/** The plain line for how an answer ended: the bot's own word in `note` (`gone`, `changed`, `not-allowed`), or a `still-waiting`. */
function sayFailure(error: unknown, undoing: boolean): string {
  if (!(error instanceof ApiError)) return w.couldNot;
  if (error.kind === "still-waiting") return w.stillWorking;
  if (error.kind === "gone" || error.note === "gone") return w.gone;
  if (undoing && error.note === "changed") return w.tooLate;
  if (error.kind === "not-allowed" || error.note === "not-allowed") return w.notAllowed;
  return w.couldNot; // (bad target, failed, lost, anything else: nothing was changed)
}

export default function SecretQuestions({ api, campaignRef, id }: { api: AccountApi; campaignRef: string; id: string }) {
  const [list, setList] = useState<SecretQuestion[] | null>(null);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [undo, setUndo] = useState<{ batch: number } | null>(null);
  const flight = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => void (live.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.secretQuestions) return;
    try {
      const got = await api.secretQuestions(campaignRef);
      if (live.current) (setList(got), setFailed(false));
    } catch {
      if (live.current) setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  async function answer(q: SecretQuestion, action: "keep" | "make"): Promise<void> {
    if (!api.answerSecret || flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    setUndo(null);
    try {
      const got = await api.answerSecret(campaignRef, { action, question: q.id });
      if (live.current) {
        const done = q[action].done;
        if (done) setSaid({ ok: true, text: done }); // (a gone item has no done line: the list says what is left)
        if (action === "make" && got.batch !== null) setUndo({ batch: got.batch });
      }
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error, false) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  async function takeBack(batch: number): Promise<void> {
    if (!api.answerSecret || flight.current) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      await api.answerSecret(campaignRef, { action: "undo", batch });
      if (live.current) (setUndo(null), setSaid({ ok: true, text: w.undone }));
    } catch (error) {
      if (live.current) setSaid({ ok: false, text: sayFailure(error, true) });
    } finally {
      await read();
      flight.current = false;
      if (live.current) setBusy(false);
    }
  }

  if (failed && !list) return <p class="warn" role="alert">{w.failed}</p>;
  if (!list) return <p class="muted" role="status">{w.loading}</p>;
  const q = list[0];
  return (
    <section id={id} class="stack" aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`}>
        {w.heading}
        {list.length > 1 ? ` (${w.waiting(list.length)})` : ""}
      </h3>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? said.text : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? said.text : ""}
      </p>
      {q ? (
        <>
          <p>
            <strong>{q.heading}</strong>
          </p>
          {q.source && <p class="small">{q.source}</p>}
          <p class="small">{w.why}</p>
          {q.keep.says && <p class="small">{q.keep.says}</p>}
          {q.make.says && <p class="small">{q.make.says}</p>}
          <div class="row">
            <button type="button" class="button secondary" disabled={busy} onClick={() => void answer(q, "keep")}>
              {q.keep.label}
            </button>
            <button type="button" class="button" disabled={busy} onClick={() => void answer(q, "make")}>
              {q.make.label}
            </button>
          </div>
          <SavingWait active={busy} stage={w.saving} />
        </>
      ) : (
        <p class="muted">{w.empty}</p>
      )}
      {/* Undo stays until the next answer, even when the make left no question on the list. */}
      {undo && (
        <div class="row">
          <button type="button" class="button secondary" disabled={busy} onClick={() => void takeBack(undo.batch)}>
            {w.undo}
          </button>
        </div>
      )}
    </section>
  );
}
