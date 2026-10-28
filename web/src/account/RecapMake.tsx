/** @jsxImportSource preact */
// Recaps for past sessions (#2610; core #2608, #2609), for a campaign's DMs: make a recap for a session
// that has none, make it again, or add one someone else wrote. The sessions come from the page's own list
// (`GET /me/campaigns/{ref}/past-sessions`, DMs only: anyone else gets "not allowed" and sees nothing). While
// a recap is made the page shows the shared wait panel (stage, how far, how long it has run, when it last
// moved; owner rule 2026-10-23). It asks one question when something would be replaced (the API's
// `needs_confirm`), and the right-to-use confirmation only when the API asks for it (`needs_rights`, once
// for a campaign). The API decides everything; this only sends what the DM chose and says what came back.
import { useEffect, useRef, useState } from "preact/hooks";

import { PRESENT_PER_SCENE_LIVE } from "../content/present-words";
import { RECAP_MAKE_LIVE, makeWords as w } from "../content/recap-make";
import { sessionDay } from "../content/recaps";
import { stageWords, workWords } from "../content/work-progress";
import { ApiError, type AccountApi, type AddRecapInput, type PastSession, type Recap } from "./api";
import WorkProgress from "./WorkProgress";

const POLL_START_MS = 3_000;
const POLL_MAX_MS = 15_000;
/** After this long with the recap still being made the page stops looking and says so. */
export const WATCH_LIMIT_MS = 30 * 60_000;
const MAX_FILE_BYTES = 1_000_000;

/** What was chosen when the API asked its one question. */
type Asking = { ref: string; what: "again" | "replace"; kept: boolean; finished?: boolean | undefined; lists?: "yes" | "maybe" | undefined; checking?: boolean | undefined; input?: AddRecapInput } | null;

function kindOf(error: unknown): string {
  return error instanceof ApiError ? error.kind : "server";
}
// An unknown kind gets the general sentence, never the code itself.
const say = (error: unknown): string => w.errors[kindOf(error)] ?? w.errors["server"] ?? "";

/** The question: a recap that is not finished (it failed) has nothing to keep, so the words do not talk about an old one. */
function askWords(q: NonNullable<Asking>): string {
  if (q.what === "again") return q.finished === false ? w.askAgainUnfinished : w.askAgain(q.kept);
  if (q.finished === false) return w.askReplaceUnfinished;
  return q.kept ? w.askReplaceExisting : w.askReplaceDraft;
}

function length(seconds: number | null | undefined): string {
  if (!seconds) return "";
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}` : `${m} min`;
}

function stateOf(s: PastSession): string {
  const r = s.recap;
  if (!r) return (s.held ? w.state["held"] : w.state["none"]) as string;
  if (r.working) return w.state["working"] as string;
  if (r.state === "failed") return w.state["failed"] as string;
  if (r.status === "posted") return w.state["posted"] as string;
  return (r.approved ? w.state["approved"] : w.state["ready"]) as string;
}

export default function RecapMake({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<PastSession[] | "loading" | "hidden">("loading");
  const [asking, setAsking] = useState<Asking>(null);
  const [adding, setAdding] = useState<PastSession | null>(null);
  // The API asked for the right-to-use confirmation: from then on the form shows the box.
  const [needRights, setNeedRights] = useState(false);
  const [running, setRunning] = useState<{ at: number; ref: string } | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  // The session whose replacement the DM already said yes to: the rights box can come after that yes, and
  // the form must not ask the question a second time.
  const confirmedReplace = useRef<string | null>(null);

  const load = async (): Promise<void> => {
    if (!api.pastSessions) return setList("hidden");
    try {
      setList((await api.pastSessions(campaignRef)).sessions);
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

  /** Whether the recap has "who was there" lists the DM saved: "yes", "maybe" when it could not be read, else nothing. */
  const savedLists = async (s: PastSession): Promise<"yes" | "maybe" | undefined> => {
    if (!PRESENT_PER_SCENE_LIVE || !api.recap) return undefined;
    try {
      return (await api.recap(s.ref)).beats?.some((b) => b.saved) ? "yes" : undefined;
    } catch {
      return "maybe";
    }
  };

  /** Show the question at once. A rewrite or a replacement clears the "who was there" lists the DM saved, so the
   * buttons wait (a moment) until the recap has been looked at and the question can say so (#2684). */
  const ask = (next: NonNullable<Asking>, s: PastSession): void => {
    if (!PRESENT_PER_SCENE_LIVE || !api.recap) {
      setAsking(next);
      return;
    }
    setAsking({ ...next, checking: true });
    void savedLists(s).then((lists) => setAsking((now) => (now && now.ref === next.ref ? { ...now, lists, checking: false } : now)));
  };

  /** Whether the question comes before anything is sent (#2764): the session has a recap of any kind, even a
   * failed one that the API would replace without a word, and its scenes have saved "who was there" lists.
   * Matches the question Discord asks (#2763). A finished recap is also asked about by the API itself. */
  const listsFirst = async (s: PastSession, next: NonNullable<Asking>): Promise<boolean> => {
    if (!s.recap || !PRESENT_PER_SCENE_LIVE || !api.recap) return false;
    const lists = await savedLists(s);
    if (!lists) return false;
    setAsking({ ...next, finished: s.recap.state === "ready", lists, checking: false });
    return true;
  };

  /** Make (or make again) the recap of a session; `replace` after the DM said yes to the question. */
  const start = async (s: PastSession, replace: boolean): Promise<void> => {
    if (inFlight.current || !api.makeRecap) return;
    inFlight.current = true;
    setBusy(true);
    setProblem("");
    try {
      if (!replace && (await listsFirst(s, { ref: s.ref, what: "again", kept: !!(s.recap?.approved || s.recap?.status === "posted") }))) return;
      const got = await api.makeRecap(s.ref, replace);
      setAsking(null);
      setRunning({ at: s.sessionAt, ref: got.ref });
    } catch (error) {
      const kind = kindOf(error);
      if (kind === "needs-confirm") ask({ ref: s.ref, what: "again", kept: !!(s.recap?.approved || s.recap?.status === "posted") }, s);
      else if (kind === "busy") await load(); // the list then shows it being made and watches it
      else {
        setAsking(null);
        setProblem(say(error));
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  /** Add a recap someone wrote. True when it was taken (the form can go). */
  const submit = async (s: PastSession, input: AddRecapInput): Promise<boolean> => {
    if (inFlight.current || !api.addRecap) return false;
    inFlight.current = true;
    setBusy(true);
    setProblem("");
    try {
      if (input.replace) confirmedReplace.current = s.ref;
      const sure = confirmedReplace.current === s.ref;
      const kept = !!(s.recap?.approved || s.recap?.status === "posted");
      if (!sure && (await listsFirst(s, { ref: s.ref, what: "replace", kept, input }))) return false;
      const got = await api.addRecap(campaignRef, sure ? { ...input, replace: true } : input);
      confirmedReplace.current = null;
      setAsking(null);
      setAdding(null);
      setRunning({ at: s.sessionAt, ref: got.ref });
      return true;
    } catch (error) {
      const kind = kindOf(error);
      if (kind === "needs-confirm") ask({ ref: s.ref, what: "replace", kept: !!(s.recap?.approved || s.recap?.status === "posted"), input }, s);
      else {
        // The rights box is in the form, which the question hides: take the question away, the yes is kept.
        if (kind === "needs-rights") {
          setNeedRights(true);
          setAsking(null);
        }
        setProblem(say(error));
      }
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const sessions = [...list].sort((a, b) => b.sessionAt - a.sessionAt);
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
            const watching = here ? running!.ref : s.recap?.working ? s.ref : null;
            const working = watching !== null;
            const question = asking?.ref === s.ref ? asking : null;
            return (
              <li key={s.ref}>
                <span class="campaign-name">{w.session(s.sessionNumber, sessionDay(s.sessionAt), length(s.sessionSeconds))}</span>
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
                ) : question || adding?.ref === s.ref ? (
                  <>
                  {question && (
                  <div class="stack" role="group" aria-label={askWords(question)}>
                    <p>{askWords(question)}</p>
                    {question.checking && (
                      <p class="small muted" role="status">
                        {w.checkingLists}
                      </p>
                    )}
                    {question.lists && <p>{question.lists === "yes" ? w.askAgainLists : w.askAgainMaybeLists}</p>}
                    <div class="row">
                      <button
                        type="button"
                        class="button"
                        disabled={busy || !!question.checking}
                        onClick={() => void (question.what === "again" ? start(s, true) : submit(s, { ...question.input!, replace: true }))}
                      >
                        {question.what === "again" ? w.yesAgain : w.yesReplace}
                      </button>
                      <button
                        type="button"
                        class="button secondary"
                        disabled={busy}
                        onClick={() => {
                          if (question.what === "replace") setAdding(null); // a clean cancel, not an empty form
                          confirmedReplace.current = null;
                          setAsking(null);
                        }}
                      >
                        {w.keep}
                      </button>
                    </div>
                  </div>
                  )}
                  {/* The form stays, out of sight, while the question is asked: the words typed are still there when the rights box comes after the yes. */}
                  {adding?.ref === s.ref && (
                    <div hidden={!!question}>
                      <AddForm session={s} needRights={needRights} busy={busy} onCancel={() => setAdding(null)} onSubmit={(input) => submit(s, input)} />
                    </div>
                  )}
                  </>
                ) : (
                  <div class="stack">
                    {!s.recap && !s.held && !s.hasTranscript && <p class="small muted">{w.noTranscript}</p>}
                    <div class="row">
                      {!s.held && (s.recap ? s.recap.canMakeAgain : s.hasTranscript) && (
                        <button type="button" class="button secondary" disabled={busy} onClick={() => void start(s, false)}>
                          {s.recap ? w.again : w.make}
                        </button>
                      )}
                      {!s.held && (
                        <button
                          type="button"
                          class="button secondary"
                          disabled={busy}
                          onClick={() => {
                            setProblem("");
                            setAdding(s);
                          }}
                        >
                          {w.add}
                        </button>
                      )}
                      {s.recap && <a href={`/account/recaps/${encodeURIComponent(s.ref)}`}>{w.open}</a>}
                    </div>
                  </div>
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
 * moves, and given up on (with a way to look again) after a long time. The recap's own progress is used
 * when the API sends it; until then its state (the bot's step) says where it is, with the time since the
 * DM asked. */
function Making({ api, recapRef, onDone, onClose }: { api: AccountApi; recapRef: string; onDone: () => void; onClose: () => void }) {
  const [recap, setRecap] = useState<Recap | null>(null);
  const [gone, setGone] = useState("");
  const started = useRef(Date.now());
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let wait = POLL_START_MS;
    let last = "";
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
        // Back off while nothing moves (the same step and the same last-moved time), start again when it does.
        const seen = `${next.state}:${next.progress?.updatedAt ?? ""}:${next.progress?.done ?? ""}`;
        wait = seen !== last ? POLL_START_MS : Math.min(wait * 2, POLL_MAX_MS);
        last = seen;
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
  const began = p?.startedAt ?? (recap?.restartedAt || recap?.uploadedAt || undefined);
  return (
    <div class="stack">
      <WorkProgress
        stage={stageWords(w.stage, p?.stage ?? recap?.state)}
        done={p?.done}
        total={p?.total}
        unit={p?.unit ?? undefined}
        startedAt={began}
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

function AddForm({
  session,
  needRights,
  busy,
  onCancel,
  onSubmit,
}: {
  session: PastSession;
  needRights: boolean;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (input: AddRecapInput) => Promise<boolean>;
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
    if (needRights && !right) return setNote(w.errors["needs-rights"] as string);
    await onSubmit({
      session: session.sessionAt,
      writtenBy: who.trim(),
      ...(how === "link" ? { link: url.trim() } : { text }),
      ...(title.trim() ? { title: title.trim() } : {}),
      ...(needRights && right ? { rights: true } : {}),
    });
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
      {needRights && (
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
