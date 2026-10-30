/** @jsxImportSource preact */
// The House-rules file on a campaign's House rules page (#2985, first slice), for the campaign's game masters: connect
// or change the file, stop using it, compare it now, or upload a text file once. The request is queued for the bot;
// the page follows it with the shared wait panel and shows counts only ("2 to add, 1 to change, 3 to remove"), never a
// rule. Review and Accept stay in Discord for now, so a result that finds a difference says where to go next. Anyone
// who is not a game master gets "not allowed" from the API and sees nothing at all.
import { useEffect, useRef, useState } from "preact/hooks";

import { HOUSE_FILE_LINK_MAX, HOUSE_FILE_TEXT_MAX, houseFilePanel as words } from "../content/house-file-words";
import { stageWords } from "../content/work-progress";
import { type AccountApi, ApiError, type HouseFileInput, type HouseFileState } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_MS = 3000;
const POLL_MAX_MS = 30_000;
/** A taken request quiet this long reads as stalled (the door puts it back at ten minutes). */
const STALLED_S = 300;

type Form = "none" | "link" | "upload" | "stop";

export default function HouseFilePanel({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [state, setState] = useState<HouseFileState | null>(null);
  const [hidden, setHidden] = useState(false);
  const [form, setForm] = useState<Form>("none");
  const [link, setLink] = useState("");
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const misses = useRef(0);
  const seen = useRef(-1);
  const idle = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const first = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);

  // What is there now, so a reload does not lose a request that is still open. A person who is not a game master (or a
  // switch that is off) gets nothing at all: the panel is simply not there.
  useEffect(() => {
    if (!api.houseFile) return;
    let live = true;
    void api
      .houseFile(campaignRef)
      .then((got) => {
        if (!live) return;
        setLoadFailed(false);
        setState(got);
      })
      .catch((err) => {
        if (!live) return;
        if (err instanceof ApiError && (err.kind === "not-allowed" || err.kind === "not-found")) setHidden(true);
        else setLoadFailed(true); // any other failure says so and offers Try again, never "Looking…" for ever
      });
    return () => {
      live = false;
    };
  }, [api, campaignRef, attempt]);

  const req = state?.request ?? null;
  const open = req !== null && (req.state === "waiting" || req.state === "working");
  useEffect(() => {
    if (!open || !req || !api.houseFile) return;
    let live = true;
    const look = async () => {
      try {
        const got = await api.houseFile?.(campaignRef, req.id);
        if (!live || !got) return;
        misses.current = 0;
        idle.current = got.request?.updatedAt === seen.current ? idle.current + 1 : 0;
        seen.current = got.request?.updatedAt ?? -1;
        setState(got);
        const r = got.request;
        if (r && (r.state === "waiting" || r.state === "working")) {
          timer.current = setTimeout(look, Math.min(POLL_MS * 2 ** Math.min(idle.current, 4), POLL_MAX_MS));
        }
      } catch {
        if (!live) return;
        // A look that fails once is tried again a few times before the page gives up (the request carries on either way).
        misses.current += 1;
        if (misses.current >= 3) setStopped(true);
        else timer.current = setTimeout(look, POLL_MS * 2 ** misses.current);
      }
    };
    timer.current = setTimeout(look, POLL_MS);
    return () => {
      live = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [api, campaignRef, open, req?.id]);

  // The question or the form takes the keyboard focus; the buttons get it back when it closes.
  useEffect(() => {
    if (form !== "none") field.current?.focus();
  }, [form]);

  if (hidden || !api.houseFile || !api.houseFileAdd) return null;
  if (!state && loadFailed)
    return (
      <div class="stack">
        <p role="alert">{words.loadFailed}</p>
        <p>
          <button type="button" class="button secondary" onClick={() => { setLoadFailed(false); setAttempt((n) => n + 1); }}>
            {words.tryAgain}
          </button>
        </p>
      </div>
    );
  if (!state) return <p class="muted" role="status">{words.loading}</p>;

  function close() {
    setForm("none");
    setProblem("");
    setTimeout(() => first.current?.focus(), 0);
  }

  async function send(input: HouseFileInput) {
    if (!api.houseFileAdd) return;
    setBusy(true);
    setProblem("");
    try {
      const got = await api.houseFileAdd(campaignRef, input);
      seen.current = -1;
      idle.current = 0;
      setStopped(false);
      setState(got);
      setForm("none");
      setLink("");
      setText("");
      setFileName("");
    } catch (err) {
      if (err instanceof ApiError && err.kind === "slow-down") {
        // One is already open: show it, never a second.
        const got = await api.houseFile?.(campaignRef).catch(() => null);
        if (got) {
          seen.current = -1;
          idle.current = 0;
          setStopped(false);
          setState(got);
          setForm("none");
        }
      } else setProblem(words.problems[err instanceof ApiError ? err.kind : ""] ?? words.genericProblem);
    } finally {
      setBusy(false);
    }
  }

  async function chooseFile(file: File | undefined) {
    setProblem("");
    setFileName("");
    setText("");
    if (!file) return;
    try {
      const read = await file.text();
      if (read.includes("\u0000")) throw new Error("binary");
      if (read.length > HOUSE_FILE_TEXT_MAX) return setProblem(words.tooBig);
      setText(read);
      setFileName(file.name);
    } catch {
      setProblem(words.fileUnreadable);
    }
  }

  const failed = req?.state === "failed";
  const done = req?.state === "done";
  const quiet = req ? state.serverNow - req.updatedAt : 0;
  const progress = failed ? "failed" : done ? "done" : quiet > STALLED_S ? "stalled" : "running";
  const differences = req ? req.toAdd + req.toChange + req.toRemove : 0;
  const compared = done && req !== null && (req.action === "check" || req.action === "upload" || req.action === "link") && req.stage === "compared";

  return (
    <section class="stack" aria-labelledby="house-file-heading">
      <h3 id="house-file-heading">{words.heading}</h3>
      <p class="small">{words.intro}</p>
      <p>{state.linked ? words.connected(state.site) : words.notConnected}</p>
      {problem && <p role="alert">{problem}</p>}
      {req && (
        <div class="stack">
          <WorkProgress
            stage={done ? words.stages.done ?? "" : stageWords(words.stages, req.stage)}
            startedAt={req.createdAt}
            updatedAt={req.updatedAt}
            serverNow={state.serverNow}
            state={progress}
            frozen={stopped}
            reason={failed ? `${words.failed} ${words.failedReasons[req.error] ?? words.failedFallback} ${words.failedNext}` : null}
          />
          {progress === "stalled" && <p role="status">{words.stalled}</p>}
          {stopped && !done && !failed && <p role="status">{words.lookFailed}</p>}
          {done && !compared && words.results[req.action] && <p role="status">{words.results[req.action]}</p>}
          {compared && (
            <div class="stack" role="status">
              <p>{differences === 0 ? words.matches : words.counts(req.toAdd, req.toChange, req.toRemove)}</p>
              {req.toRemove > 0 && <p>{words.removing}</p>}
              {differences > 0 && (
                <>
                  <p>{words.next}</p>
                  <p class="small muted">{words.nextFallback}</p>
                </>
              )}
            </div>
          )}
        </div>
      )}
      {form === "none" && (
        <p>
          <button type="button" class="button secondary" ref={first} disabled={busy || open} onClick={() => setForm("link")}>
            {state.linked ? words.change : words.connect}
          </button>{" "}
          {state.linked && (
            <button type="button" class="button secondary" disabled={busy || open} onClick={() => void send({ action: "check" })}>
              {words.compare}
            </button>
          )}{" "}
          <button type="button" class="button secondary" disabled={busy || open} onClick={() => setForm("upload")}>
            {words.upload}
          </button>{" "}
          {state.linked && (
            <button type="button" class="button secondary" disabled={busy || open} onClick={() => setForm("stop")}>
              {words.stop}
            </button>
          )}
        </p>
      )}
      {form === "link" && (
        <form
          class="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (!link.trim()) return setProblem(words.nothingYet);
            void send({ action: "link", link: link.trim() });
          }}
        >
          <label>
            {words.linkLabel}
            <input type="url" ref={field} value={link} maxLength={HOUSE_FILE_LINK_MAX} autocomplete="off" onInput={(e) => setLink((e.currentTarget as HTMLInputElement).value)} />
          </label>
          <p class="small muted">{words.linkHelp}</p>
          <p>
            <button type="submit" class="button" disabled={busy}>{words.linkSend}</button>{" "}
            <button type="button" class="button secondary" onClick={close}>{words.cancel}</button>
          </p>
        </form>
      )}
      {form === "upload" && (
        <form
          class="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return setProblem(words.nothingYet);
            void send({ action: "upload", text });
          }}
        >
          <label>
            {words.upload}
            <input type="file" accept=".txt,text/plain" ref={field} onChange={(e) => void chooseFile((e.currentTarget as HTMLInputElement).files?.[0])} />
          </label>
          {fileName && <p class="small">{fileName}</p>}
          <p class="small muted">{words.uploadHelp}</p>
          <p>
            <button type="submit" class="button" disabled={busy}>{words.uploadSend}</button>{" "}
            <button type="button" class="button secondary" onClick={close}>{words.cancel}</button>
          </p>
        </form>
      )}
      {form === "stop" && (
        <div class="stack" role="group" aria-label={words.confirmLabel}>
          <p>{words.stopAsk}</p>
          <p>
            <button type="button" class="button" ref={(el) => { if (el) el.focus(); }} disabled={busy} onClick={() => void send({ action: "unlink" })}>
              {words.stopYes}
            </button>{" "}
            <button type="button" class="button secondary" onClick={close}>{words.keep}</button>
          </p>
        </div>
      )}
    </section>
  );
}
