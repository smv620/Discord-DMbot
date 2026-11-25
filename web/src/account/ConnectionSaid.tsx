/** @jsxImportSource preact */
// "Say it in your own words" on a connection's panel (#5293, the website half of #2940; core #5298, behind CONNECTION_SAID_LIVE).
// The DM writes up to 1,000 characters about two names. The web API only queues them; the bot reads them once and this page polls
// for what it understood: each connection as one plain sentence built from its fields (never the AI's own sentence), who knows it,
// and the DM's choice. Nothing is written until Use this, and one Undo takes it all back. The words are Discord's exactly
// (content/connection-said-words). The DM decides: DMbot only reads. The wait shows its stage, its time and its last move, and a
// failure says what happened and what to do next. Its own file, loaded only when the DM opens it, so the Story Graph keeps its weight.
import { useEffect, useRef, useState } from "preact/hooks";

// What Discord counts: the words with their spaces collapsed.
const tidy = (s: string): string => s.split(/\s+/).filter(Boolean).join(" ");

import { SAID_MAX, saidWords as w } from "../content/connection-said-words";
import { type AccountApi, ApiError, type ConnectionSaidRead } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 1500;
const POLL_LIMIT = 80; // about two minutes: a read can take up to ninety seconds

type Phase =
  | "form"
  | "reading"
  | "choose"
  | "using"
  | "used"
  | "undoing"
  | "undone"
  | "stuck";

export default function ConnectionSaid({
  api,
  refId,
  a,
  b,
  edit,
  onClose,
  onChanged,
}: {
  api: AccountApi;
  refId: string;
  /** The two entries' ids. */
  a: string;
  b: string;
  /** The id of the connection being corrected, when this is a correction. */
  edit?: string | undefined;
  onClose: () => void;
  /** Something was written or taken back: the page reads its graph again. */
  onChanged: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("form");
  const [text, setText] = useState("");
  const [problem, setProblem] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [seen, setSeen] = useState<ConnectionSaidRead | null>(null); // the last look (the wait panel reads it)
  const [read, setRead] = useState<ConnectionSaidRead | null>(null); // the ready read (what DMbot understood)
  const [picks, setPicks] = useState<Set<number>>(new Set());
  const [who, setWho] = useState<"everyone" | "dm" | "some" | "keep">(
    "everyone",
  );
  const [partly, setPartly] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const waitBox = useRef<HTMLDivElement>(null);
  const lastJob = useRef<"use" | "undo">("use");
  const statusBox = useRef<HTMLParagraphElement>(null);
  const readBox = useRef<HTMLParagraphElement>(null);
  const sending = useRef(false); // (a double press sends once)
  const floor = useRef(0); // the last row's time before a press: a finished row no newer than it is the earlier job's, not this one's

  const waiting =
    phase === "reading" || phase === "using" || phase === "undoing";
  const job = phase === "reading" ? "read" : phase === "using" ? "use" : "undo";

  // The wait takes focus, so a screen reader hears that DMbot is working and the pressed button's loss is not a loss.
  useEffect(() => {
    if (waiting) waitBox.current?.focus();
  }, [waiting]);

  // Poll the one answer until the job we are waiting for is over (the door may still show the earlier job for a moment).
  useEffect(() => {
    if (!waiting || !answer || !api.saidRead) return;
    let live = true;
    let tries = 0;
    let errors = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const look = async (): Promise<void> => {
      tries += 1;
      try {
        const got = await api.saidRead!(refId, answer);
        if (!live) return;
        errors = 0;
        // Only this job's own row is shown or believed: the door may still show the earlier job for a moment, and a
        // finished row no newer than the one seen before the press is the earlier job's (core's job id would be surer).
        const mine =
          got.job === job &&
          (job === "read" ||
            (got.updatedAt ?? 0) > floor.current ||
            (got.stage !== "done" && got.stage !== "failed"));
        if (mine) setSeen(got);
        if (mine && got.stage === "failed")
          return failed(got.reason ?? "failed");
        if (mine && got.stage === "done") return finished(got);
      } catch (error) {
        if (!live) return;
        errors += 1; // (one failed look is not the end of the wait: the work may still be running)
        if (errors >= 3) {
          const kind = error instanceof ApiError ? error.kind : "network";
          return back(w.refused[kind] ?? w.refused.server ?? "");
        }
      }
      if (tries >= POLL_LIMIT) return back(w.stalled);
      timer = setTimeout(() => void look(), POLL_MS);
    };
    const failed = (reason: string): void => {
      // A failed read goes back to the form with the words kept; a failed use leaves the read to choose again (nothing was
      // written); a failed undo stays on "done", so Use this can never be pressed a second time.
      const said = w.failed[reason] ?? w.failed.failed ?? "";
      if (job === "read") {
        setProblem(
          reason === "not_on_plan" || reason === "not_yours"
            ? said
            : `${said} ${w.failedDo}`,
        );
        setPhase("form");
        setTimeout(() => box.current?.focus(), 0);
      } else if (job === "use") {
        setProblem(`${said} ${w.failedKeep}`);
        setPhase("choose");
      } else {
        setProblem(w.undoFailed);
        setPhase("used");
      }
    };
    const back = (said: string): void => {
      // A read that never answered goes back to the form. A use or undo that did not answer may still have finished, so it
      // only says so and never offers Use this again.
      if (job === "read") {
        setProblem(said);
        setPhase("form");
        setTimeout(() => box.current?.focus(), 0);
      } else {
        lastJob.current = job;
        setProblem(w.stuck);
        setPhase("stuck");
      }
    };
    const finished = (got: ConnectionSaidRead): void => {
      if (job === "read") {
        setRead(got);
        setPicks(new Set((got.proposals ?? []).map((p) => p.number)));
        setWho(got.whoKnows ?? "everyone");
        setProblem("");
        setPhase("choose");
        setTimeout(() => readBox.current?.focus(), 0);
        return;
      } else if (job === "use") {
        setProblem("");
        setPhase("used");
        onChanged();
      } else if (got.undoError) {
        setProblem(w.undoFailed);
        setPhase("used");
      } else {
        setPartly(got.partly === true);
        setProblem("");
        setPhase("undone");
        onChanged();
      }
      setTimeout(() => statusBox.current?.focus(), 0);
    };
    timer = setTimeout(() => void look(), POLL_MS / 3);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
    // The page's callbacks are not dependencies: this runs once for each wait.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting, answer, job]);

  async function send(): Promise<void> {
    const said = tidy(text);
    if (said === "") return setProblem(w.empty);
    if (said.length > SAID_MAX) return setProblem(w.tooLong);
    if (!api.saidSend || sending.current) return;
    sending.current = true;
    setProblem("");
    try {
      const got = await api.saidSend(refId, {
        a,
        b,
        text: said,
        ...(edit ? { edit } : {}),
      });
      setAnswer(got.answer);
      setSeen(null);
      setPhase("reading");
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      setProblem(w.refused[kind] ?? w.refused.server ?? "");
    } finally {
      sending.current = false;
    }
  }

  async function use(): Promise<void> {
    if (!answer || !read || !api.saidUse || sending.current) return;
    if (picks.size === 0) return setProblem(w.pickOne);
    sending.current = true;
    floor.current = read.updatedAt ?? 0;
    setProblem("");
    try {
      await api.saidUse(refId, answer, {
        picks: [...picks].sort((x, y) => x - y),
        who,
        characters:
          who === "some" ? (read.characters ?? []).map((c) => c.id) : [],
      });
      setSeen(null);
      setPhase("using");
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      setProblem(w.refused[kind] ?? w.refused.server ?? "");
    } finally {
      sending.current = false;
    }
  }

  async function undo(): Promise<void> {
    if (!answer || !api.saidUndo || sending.current) return;
    sending.current = true;
    floor.current = seen?.updatedAt ?? 0;
    setProblem("");
    try {
      await api.saidUndo(refId, answer);
      setSeen(null);
      setPhase("undoing");
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "network";
      setProblem(w.refused[kind] ?? w.refused.server ?? "");
    } finally {
      sending.current = false;
    }
  }

  const names = (read?.characters ?? []).map((c) => c.name).join(", ");
  const secretKept =
    Boolean(read?.replacing) && (read?.proposals ?? []).some((p) => p.secret);
  return (
    <section class="stack" aria-labelledby={`said-${a}-${b}`}>
      <h4 id={`said-${a}-${b}`}>{w.heading}</h4>
      {problem && (
        <p class="warn" role="alert">
          {problem}
        </p>
      )}
      {phase === "form" && (
        <div class="stack">
          <label class="field" for={`said-text-${a}-${b}`}>
            {w.label}
          </label>
          <textarea
            id={`said-text-${a}-${b}`}
            ref={box}
            rows={4}
            maxLength={SAID_MAX + 200}
            value={text}
            placeholder={w.placeholder}
            aria-describedby={`said-count-${a}-${b}`}
            onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
          />
          <p class="small muted" id={`said-count-${a}-${b}`}>
            {w.count(tidy(text).length, SAID_MAX)}
          </p>
          <div class="row">
            <button type="button" class="button" onClick={() => void send()}>
              {w.send}
            </button>
            <button type="button" class="button secondary" onClick={onClose}>
              {w.close}
            </button>
          </div>
        </div>
      )}
      {waiting && (
        <div ref={waitBox} tabIndex={-1}>
          <WorkProgress
            stage={
              phase === "using"
                ? w.using
                : phase === "undoing"
                  ? w.undoing
                  : (w.stages[seen?.stage ?? "waiting"] ?? w.working)
            }
            startedAt={seen?.startedAt}
            updatedAt={seen?.updatedAt}
            serverNow={seen?.serverNow}
            state={seen?.state === "stalled" ? "stalled" : "running"}
            whatToDo={w.stalled}
          />
        </div>
      )}
      {phase === "choose" && read && (
        <div class="stack">
          <p tabIndex={-1} ref={readBox}>
            <strong>{w.readAs}</strong>
          </p>
          <ul class="stack" aria-label={w.readAs}>
            {(read.proposals ?? []).map((p) => (
              <li key={p.number}>
                <label>
                  <input
                    type="checkbox"
                    checked={picks.has(p.number)}
                    onChange={(e) => {
                      const next = new Set(picks);
                      if ((e.target as HTMLInputElement).checked)
                        next.add(p.number);
                      else next.delete(p.number);
                      setPicks(next);
                    }}
                  />{" "}
                  {p.sentence}
                </label>
              </li>
            ))}
          </ul>
          <fieldset class="stack">
            <legend>{w.whoHeading}</legend>
            {(["everyone", "dm"] as const).map((v) => (
              <label key={v}>
                <input
                  type="radio"
                  name={`said-who-${a}-${b}`}
                  checked={who === v}
                  onChange={() => setWho(v)}
                />{" "}
                {w.who[v]}
              </label>
            ))}
            {names !== "" && (
              <label>
                <input
                  type="radio"
                  name={`said-who-${a}-${b}`}
                  checked={who === "some"}
                  onChange={() => setWho("some")}
                />{" "}
                {w.who.some(names)}
              </label>
            )}
            {secretKept && (
              <label>
                <input
                  type="radio"
                  name={`said-who-${a}-${b}`}
                  checked={who === "keep"}
                  onChange={() => setWho("keep")}
                />{" "}
                {w.who.keep}
              </label>
            )}
          </fieldset>
          {(read.leftOut ?? []).length > 0 && (
            <p class="small">
              {w.leftOut((read.leftOut ?? []).map((n) => `“${n}”`).join(", "))}
              {who === "dm" ? ` ${w.leftOutKept}` : ""}
            </p>
          )}
          <p class="small muted">
            {w.nothingYet} {read.replacing ? w.replaces : w.canUndo}
          </p>
          <div class="row">
            <button type="button" class="button" onClick={() => void use()}>
              {w.use}
            </button>
            <button
              type="button"
              class="button secondary"
              onClick={() => setPhase("form")}
            >
              {w.again}
            </button>
            <button type="button" class="button secondary" onClick={onClose}>
              {w.close}
            </button>
          </div>
        </div>
      )}
      {phase === "used" && (
        <div class="stack">
          <p role="status" tabIndex={-1} ref={statusBox}>
            {w.used}
          </p>
          <div class="row">
            {(seen?.canUndo ?? true) && (
              <button
                type="button"
                class="button secondary"
                onClick={() => void undo()}
              >
                {w.undo}
              </button>
            )}
            <button type="button" class="button secondary" onClick={onClose}>
              {w.close}
            </button>
          </div>
        </div>
      )}
      {phase === "stuck" && (
        <div class="row">
          <button
            type="button"
            class="button"
            onClick={() => (
              setProblem(""),
              setPhase(lastJob.current === "use" ? "using" : "undoing")
            )}
          >
            {w.checkAgain}
          </button>
          <button type="button" class="button secondary" onClick={onClose}>
            {w.close}
          </button>
        </div>
      )}
      {phase === "undone" && (
        <div class="stack">
          <p role="status" tabIndex={-1} ref={statusBox}>
            {partly ? w.undonePartly : w.undone}
          </p>
          <button type="button" class="button secondary" onClick={onClose}>
            {w.close}
          </button>
        </div>
      )}
    </section>
  );
}
