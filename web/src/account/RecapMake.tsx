/** @jsxImportSource preact */
// Recaps for past sessions (#2610; core #2608, #2609), for a campaign's DMs: make a recap for a session
// that has none, make it again, or add one someone else wrote. The API decides who may and answers a
// player "not allowed", in which case nothing shows. While a recap is made the page shows the shared wait
// panel (stage, how far, how long it has run, when it last moved; owner rule 2026-10-23). It asks one
// question when something would be replaced, and the right-to-use confirmation only the first time.
import { useEffect, useRef, useState } from "preact/hooks";

import { RECAP_MAKE_LIVE, makeWords as w } from "../content/recap-make";
import { sessionDay } from "../content/recaps";
import { stageWords, workWords } from "../content/work-progress";
import { ApiError, type AccountApi, type PastSession, type PastSessions, type Recap } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_START_MS = 3_000;
const POLL_MAX_MS = 15_000;
/** After this long with the recap still being made the page stops looking and says so. */
export const WATCH_LIMIT_MS = 30 * 60_000;
const MAX_FILE_BYTES = 1_000_000;

type Asking = { at: number; what: "again" | "replace"; input?: Parameters<NonNullable<AccountApi["addRecap"]>>[2] } | null;

function kindOf(error: unknown): string {
  return error instanceof ApiError ? error.kind : "server";
}
const say = (error: unknown): string => w.errors[kindOf(error)] ?? w.errors["server"] ?? "";

function length(seconds: number | null | undefined): string {
  if (!seconds) return "";
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}` : `${m} min`;
}

function stateOf(s: PastSession): string {
  const r = s.recap;
  if (!r) return w.state["none"] as string;
  if (r.state === "working") return w.state["working"] as string;
  if (r.state === "failed") return w.state["failed"] as string;
  if (r.status === "posted") return w.state["posted"] as string;
  if (r.held) return w.state["held"] as string;
  return (r.approved ? w.state["approved"] : w.state["ready"]) as string;
}

export default function RecapMake({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<PastSessions | "loading" | "hidden">("loading");
  const [asking, setAsking] = useState<Asking>(null);
  const [adding, setAdding] = useState<number | null>(null);
  const [running, setRunning] = useState<{ at: number; ref: string } | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  const load = async (): Promise<void> => {
    if (!api.pastSessions) return setList("hidden");
    try {
      setList(await api.pastSessions(campaignRef));
    } catch {
      setList("hidden"); // a player, a signed-out page or an older API: nothing to offer
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  if (!RECAP_MAKE_LIVE || !api.makeRecap || list === "hidden") return null;
  if (list === "loading")
    return (
      <p class="small muted" role="status">
        {w.loading}
      </p>
    );

  const start = async (at: number, replaceApproved: boolean): Promise<void> => {
    if (inFlight.current || !api.makeRecap) return;
    inFlight.current = true;
    setBusy(true);
    setProblem("");
    try {
      const got = await api.makeRecap(campaignRef, at, replaceApproved);
      setAsking(null);
      setRunning({ at, ref: got.ref });
    } catch (error) {
      if (kindOf(error) === "approved-exists") setAsking({ at, what: "again" });
      else if (kindOf(error) === "already-running") await load(); // the list then shows it working and watches it
      else {
        setAsking(null);
        setProblem(say(error));
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const submit = async (at: number, input: NonNullable<Asking>["input"]): Promise<boolean> => {
    if (inFlight.current || !api.addRecap || !input) return false;
    inFlight.current = true;
    setBusy(true);
    setProblem("");
    try {
      const got = await api.addRecap(campaignRef, at, input);
      setAsking(null);
      setAdding(null);
      setRunning({ at, ref: got.ref });
      return true;
    } catch (error) {
      if (kindOf(error) === "recap-exists") setAsking({ at, what: "replace", input });
      else setProblem(say(error));
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const sessions = [...list.sessions].sort((a, b) => b.sessionAt - a.sessionAt);
  return (
    <section class="panel stack" aria-labelledby="recap-make-heading">
      <h2 id="recap-make-heading">{w.heading}</h2>
      <p class="small">{w.lead}</p>
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
      {sessions.length === 0 ? (
        <p class="small muted">{w.none}</p>
      ) : (
        <ul class="campaigns">
          {sessions.map((s) => {
            const here = running?.at === s.sessionAt;
            // A recap that is already being made when the page opens is watched like one just started.
            const watching = here ? running!.ref : s.recap?.state === "working" ? s.recap.ref : null;
            const working = watching !== null;
            return (
              <li key={s.sessionAt}>
                <span class="campaign-name">
                  {w.session(s.sessionNumber, sessionDay(s.sessionAt), length(s.sessionSeconds))}
                </span>
                <span class="tags">
                  <span class="tag">{working ? (w.state["working"] as string) : stateOf(s)}</span>
                </span>
                {watching ? (
                  <Making
                    key={watching}
                    api={api}
                    recapRef={watching}
                    onDone={() => {
                      setRunning(null);
                      void load();
                    }}
                    onClose={() => {
                      setRunning(null);
                      void load();
                    }}
                  />
                ) : asking?.at === s.sessionAt ? (
                  <div class="stack" role="group" aria-label={asking.what === "again" ? w.askReplaceApproved : w.askReplaceExisting}>
                    <p>{asking.what === "again" ? w.askReplaceApproved : w.askReplaceExisting}</p>
                    <div class="row">
                      <button
                        type="button"
                        class="button"
                        disabled={busy}
                        onClick={() => void (asking.what === "again" ? start(s.sessionAt, true) : submit(s.sessionAt, { ...asking.input!, replace: true }))}
                      >
                        {asking.what === "again" ? w.yesAgain : w.yesReplace}
                      </button>
                      <button
                        type="button"
                        class="button secondary"
                        disabled={busy}
                        onClick={() => {
                          if (asking.what === "replace") setAdding(null); // a clean cancel, not an empty form
                          setAsking(null);
                        }}
                      >
                        {w.keep}
                      </button>
                    </div>
                  </div>
                ) : adding === s.sessionAt ? (
                  <AddForm
                    needRight={!list.rightConfirmed}
                    busy={busy}
                    onCancel={() => setAdding(null)}
                    onSubmit={(input) => submit(s.sessionAt, input)}
                  />
                ) : (
                  !working && (
                    <div class="row">
                      <button type="button" class="button secondary" disabled={busy} onClick={() => void start(s.sessionAt, false)}>
                        {s.recap ? w.again : w.make}
                      </button>
                      <button
                        type="button"
                        class="button secondary"
                        disabled={busy}
                        onClick={() => {
                          setProblem("");
                          setAdding(s.sessionAt);
                        }}
                      >
                        {w.add}
                      </button>
                      {s.recap && (
                        <a href={`/account/recaps/${encodeURIComponent(s.recap.ref)}`}>{w.open}</a>
                      )}
                    </div>
                  )
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Watches one recap being made: the shared wait panel, asked again a little less often while nothing
 * moves, and given up on (with a way to look again) after a long time. */
function Making({ api, recapRef, onDone, onClose }: { api: AccountApi; recapRef: string; onDone: () => void; onClose: () => void }) {
  const [recap, setRecap] = useState<Recap | null>(null);
  const [gone, setGone] = useState("");
  const started = useRef(Date.now());
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let wait = POLL_START_MS;
    let last = -1;
    const tick = async (): Promise<void> => {
      if (!api.recap) return setGone(w.errors["server"] as string);
      try {
        const next = await api.recap(recapRef);
        if (!live) return;
        setRecap(next);
        if (!next.working) {
          if (next.state !== "failed") onDone();
          return;
        }
        const stamp = next.progress?.updatedAt ?? 0;
        wait = stamp > last ? POLL_START_MS : Math.min(wait * 2, POLL_MAX_MS);
        last = Math.max(last, stamp);
        if (Date.now() - started.current > WATCH_LIMIT_MS) return setGone(w.stoppedWatching);
        timer = setTimeout(() => void tick(), wait);
      } catch (error) {
        if (live) setGone(say(error));
      }
    };
    void tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, recapRef]);

  const p = recap?.progress;
  const failed = !!recap && !recap.working && recap.state === "failed";
  return (
    <div class="stack">
      <WorkProgress
        stage={stageWords(w.stage, p?.stage)}
        done={p?.done}
        total={p?.total}
        unit={p?.unit ?? undefined}
        startedAt={p?.startedAt}
        updatedAt={p?.updatedAt}
        serverNow={p?.serverNow ?? recap?.serverNow}
        state={failed ? "failed" : p?.state === "stalled" ? "stalled" : "running"}
        frozen={gone !== ""}
        reason={failed ? (p?.reason ? (w.failedReason[p.reason] ?? workWords.failed) : recap?.error || null) : null}
      />
      {gone && <p class="note">{gone}</p>}
      {(failed || gone) && (
        <div class="row">
          <button type="button" class="button secondary" onClick={onClose}>
            {w.close}
          </button>
          <a href={`/account/recaps/${encodeURIComponent(recapRef)}`}>{w.open}</a>
        </div>
      )}
    </div>
  );
}

type FormInput = NonNullable<Asking>["input"];

function AddForm({
  needRight,
  busy,
  onCancel,
  onSubmit,
}: {
  needRight: boolean;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (input: FormInput) => Promise<boolean>;
}) {
  const f = w.form;
  const [how, setHow] = useState<"paste" | "file" | "link">("paste");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [who, setWho] = useState("");
  const [title, setTitle] = useState("");
  const [right, setRight] = useState(false);
  const [note, setNote] = useState("");
  const headRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => headRef.current?.focus(), []);

  const readFile = async (file: File | undefined): Promise<void> => {
    setNote("");
    setText(""); // an earlier file's words are never sent after a later one failed
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) return setNote(w.fileTooBig);
    try {
      setText(await file.text());
    } catch {
      setNote(w.errors["server"] as string);
    }
  };
  const send = async (event: Event): Promise<void> => {
    event.preventDefault();
    setNote("");
    if (needRight && !right) return setNote(w.errors["right-not-confirmed"] as string);
    const input: NonNullable<FormInput> = {
      writtenBy: who.trim(),
      ...(title.trim() ? { title: title.trim() } : {}),
      ...(how === "link" ? { url: url.trim() } : { text }),
      ...(needRight ? { confirmRight: true } : {}),
    };
    await onSubmit(input);
  };

  return (
    <form class="stack" onSubmit={(e) => void send(e)} aria-labelledby="recap-add-heading">
      <h3 id="recap-add-heading" tabIndex={-1} ref={headRef}>
        {f.heading}
      </h3>
      <fieldset>
        <legend>{f.how}</legend>
        {(["paste", "file", "link"] as const).map((k) => (
          <label key={k}>
            <input type="radio" name="recap-how" checked={how === k} onChange={() => setHow(k)} /> {k === "paste" ? f.paste : k === "file" ? f.file : f.link}
          </label>
        ))}
      </fieldset>
      {how === "paste" && (
        <label>
          {f.text}
          <textarea rows={8} value={text} onInput={(e) => setText((e.target as HTMLTextAreaElement).value)} required />
        </label>
      )}
      {how === "file" && (
        <label>
          {f.file}
          <input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={(e) => void readFile((e.target as HTMLInputElement).files?.[0])} required />
          <span class="small muted">{f.fileHelp}</span>
        </label>
      )}
      {how === "link" && (
        <label>
          {f.link_}
          <input type="url" value={url} onInput={(e) => setUrl((e.target as HTMLInputElement).value)} required />
          <span class="small muted">{f.linkHelp}</span>
        </label>
      )}
      <label>
        {f.who}
        <input type="text" value={who} onInput={(e) => setWho((e.target as HTMLInputElement).value)} maxLength={80} required />
        <span class="small muted">{f.whoHelp}</span>
      </label>
      <label>
        {f.title}
        <input type="text" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} maxLength={120} />
      </label>
      {needRight && (
        <label>
          <input type="checkbox" checked={right} onChange={(e) => setRight((e.target as HTMLInputElement).checked)} /> {f.right}
          <span class="small muted"> {f.rightNote}</span>
        </label>
      )}
      {note && (
        <p class="note bad" role="alert">
          {note}
        </p>
      )}
      <div class="row">
        <button type="submit" class="button" disabled={busy} aria-busy={busy}>
          {busy ? f.working : f.submit}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={onCancel}>
          {f.cancel}
        </button>
      </div>
    </form>
  );
}
