/** @jsxImportSource preact */
// "Match names" on "Your adventure" (#3694 slice G1, behind ADVENTURE_LIVE; core #5318): Discord's 🔗 Match names. The bot lists pairs, a name from the
// DM's adventure beside a name DMbot remembers from the game, and the DM says for each whether they are the same (DMbot never guesses). Up to ten
// answers go in one save; one Undo takes that save back. Every ask is queued for the bot and polled, showing its stage, how long and when it last
// moved. The names on the screen are the book's own and the game's: DM's page only, never put in an address, a log or storage. The words are
// Discord's (ui/book_link.py); a refusal is a code from core, worded here.
import { useEffect, useRef, useState } from "preact/hooks";

import { adventureWords as w } from "../content/adventure-words";
import { type AdventureApi, ApiError, type AdventureMatch, type AdventureMatchPair, type AdventureMatchPick } from "./api";
import WorkProgress from "./WorkProgress";

const FIRST_MS = 500;
const EVERY_MS = 1500;
const LOOKS = 60;
const PICKS_MAX = 10; // one save holds at most ten answers (core's limit, and Discord's one message)

type Body = Parameters<AdventureApi["adventureMatch"]>[1];
type Answer = "same" | "not_same" | "";

export default function AdventureMatchPanel({ api, refId }: { api: AdventureApi; refId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [seen, setSeen] = useState<AdventureMatch | null>(null);
  const [pairs, setPairs] = useState<AdventureMatchPair[] | null>(null); // null: not looked yet
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [said, setSaid] = useState("");
  const [problem, setProblem] = useState("");
  const [undoOf, setUndoOf] = useState<string | null>(null); // the save that can be taken back
  const epoch = useRef(0);
  const sending = useRef(false);
  const waitBox = useRef<HTMLDivElement>(null);
  const headBox = useRef<HTMLHeadingElement>(null);
  const askBox = useRef<HTMLButtonElement>(null);
  const keyOf = (p: AdventureMatchPair): string => `${p.planId}|${p.entityId}`;

  useEffect(() => {
    return () => {
      epoch.current += 1; // leaving the page or another campaign ends the watching
      sending.current = false;
    };
  }, [api, refId]);
  useEffect(() => {
    if (busy) waitBox.current?.focus();
  }, [busy]);

  /** Queue one ask and poll it to its end; `done` gets the finished job (with its id), or null after the problem has been shown. */
  async function run(body: Body, done: (got: AdventureMatch, id: string) => void, slow: string = w.matchSlow): Promise<void> {
    if (sending.current) return;
    sending.current = true;
    const mine = epoch.current;
    const here = (): boolean => mine === epoch.current;
    setBusy(true);
    setProblem("");
    setSeen(null);
    const stop = (text: string): void => {
      if (!here()) return;
      setBusy(false);
      setProblem(text);
    };
    try {
      let id: string;
      try {
        id = (await api.adventureMatch(refId, body)).match;
      } catch (error) {
        return stop(w.matchRefused[error instanceof ApiError ? error.kind : "network"] ?? w.matchRefused.server ?? "");
      }
      let errors = 0;
      for (let i = 0; i < LOOKS; i += 1) {
        await new Promise((ok) => setTimeout(ok, i === 0 ? FIRST_MS : EVERY_MS));
        if (!here()) return;
        try {
          const got = await api.adventureMatching(refId, id);
          errors = 0;
          if (!here()) return;
          setSeen(got);
          if (got.state === "failed") return stop(w.matchFailed[got.error] ?? w.matchFailed.failed ?? "");
          if (got.state === "done") {
            setBusy(false);
            done(got, id);
            return;
          }
        } catch (error) {
          if (error instanceof ApiError && (error.kind === "not-found" || error.kind === "signed-out")) return stop(slow);
          errors += 1;
          if (errors >= 3) return stop(slow);
        }
      }
      stop(slow);
    } finally {
      if (here()) sending.current = false;
    }
  }

  function look(): void {
    setOpen(true);
    setSaid("");
    setUndoOf(null);
    void run({ job: "suggest" }, (got) => {
      setPairs(got.answer?.pairs ?? []);
      setAnswers({});
      setTimeout(() => headBox.current?.focus(), 0);
    });
  }

  function save(): void {
    const picks: AdventureMatchPick[] = (pairs ?? [])
      .filter((p) => answers[keyOf(p)])
      .slice(0, PICKS_MAX)
      .map((p) => [p.planId, p.entityId, answers[keyOf(p)] as "same" | "not_same"]);
    if (picks.length === 0) return setProblem(w.matchNone);
    void run({ job: "decide", picks }, (got, id) => {
      const written = got.answer?.written ?? 0;
      const refusal = got.answer?.refusal ?? "";
      setSaid(written > 0 ? w.matchSaved(written) : w.matchSavedNone);
      setProblem(refusal ? (w.matchRefusals[refusal] ?? w.matchRefusals.failed ?? "") : "");
      setUndoOf(got.canUndo ? id : null);
      // Only the pairs that were written leave the list (the first `written` picks, in the order sent): the one that stopped the save, and any after it, stay.
      const gone = picks.slice(0, written);
      setPairs((now) => (now ?? []).filter((p) => !gone.some(([b, t]) => b === p.planId && t === p.entityId)));
      setTimeout(() => headBox.current?.focus(), 0);
    }, w.matchSlowSave);
  }

  function undo(): void {
    if (!undoOf) return;
    void run({ job: "undo", source: undoOf }, (got) => {
      const note = got.answer?.note ?? "";
      setSaid(note ? (w.matchUndoNotes[note] ?? w.matchUndone) : w.matchUndoneLook);
      setUndoOf(null);
      setTimeout(() => headBox.current?.focus(), 0);
    });
  }

  function close(): void {
    setOpen(false);
    setPairs(null);
    setSaid("");
    setProblem("");
    setUndoOf(null);
    setTimeout(() => askBox.current?.focus(), 0);
  }

  const shown = (pairs ?? []).slice(0, PICKS_MAX);
  return (
    <div class="stack">
      {!open && (
        <button type="button" class="button secondary" ref={askBox} onClick={look}>
          {w.match}
        </button>
      )}
      {open && (
        <section class="stack" aria-labelledby={`adv-match-${refId}`}>
          <h4 id={`adv-match-${refId}`} tabIndex={-1} ref={headBox}>
            {w.match}
          </h4>
          <p class="small muted">{w.matchHead}</p>
          {busy && (
            <div ref={waitBox} tabIndex={-1}>
              <WorkProgress
                stage={w.matchStages[seen?.state ?? "waiting"] ?? w.matchLooking}
                startedAt={seen?.createdAt}
                updatedAt={seen?.updatedAt}
                serverNow={seen?.serverNow}
                state="running"
              />
            </div>
          )}
          {problem && (
            <p class="warn" role="alert">
              {problem}
            </p>
          )}
          <p role="status">{said}</p>
          {!busy && pairs !== null && shown.length === 0 && !said && <p>{w.matchNothing}</p>}
          {!busy && shown.length > 0 && (
            <div class="stack">
              <p class="small muted">{w.matchHow}</p>
              {(pairs ?? []).length > PICKS_MAX && <p class="small muted">{w.matchFirst(PICKS_MAX)}</p>}
              <ul class="stack">
                {shown.map((p) => {
                  const key = keyOf(p);
                  const name = `adv-match-${key}`;
                  return (
                    <li key={key} style={{ overflowWrap: "anywhere" }}>
                      <fieldset class="stack">
                        <legend>{w.matchPair(p.planName, p.planKind, p.entityName)}</legend>
                        {(["same", "not_same", ""] as const).map((v) => (
                          <label key={v || "skip"}>
                            <input type="radio" name={name} checked={(answers[key] ?? "") === v} onChange={() => setAnswers({ ...answers, [key]: v })} />{" "}
                            {v === "same" ? w.matchSame : v === "not_same" ? w.matchNot : w.matchSkip}
                          </label>
                        ))}
                      </fieldset>
                    </li>
                  );
                })}
              </ul>
              <button type="button" class="button" onClick={save}>
                {w.matchSave}
              </button>
            </div>
          )}
          <div class="row">
            {!busy && undoOf && (
              <button type="button" class="button secondary" onClick={undo}>
                {w.matchUndo}
              </button>
            )}
            {!busy && (
              <button type="button" class="button secondary" onClick={look}>
                {w.matchAgain}
              </button>
            )}
            <button type="button" class="button secondary" disabled={busy} onClick={close}>
              {w.matchHide}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
