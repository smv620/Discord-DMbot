/** @jsxImportSource preact */
// The session transcripts (#1501): every campaign with a saved transcript in the person's servers,
// a campaign's sessions, and one session to read in two versions with a download. Anyone in the
// Discord server may read them (CLAUDE.md), so this is for players as much as DMs; the API decides
// who sees what, and the text is the bot's own export, so nothing is formatted here.
import { useEffect, useRef, useState } from "preact/hooks";

import { IMPORT_LIVE, importPage } from "../content/import-words";
import { sessionDay } from "../content/recaps";
import { PUT_BACK_READS_AGAIN, offTopic, problem, text, versionNote } from "../content/transcripts";
import {
  ApiError,
  type AccountApi,
  type StretchLine,
  type TranscriptCampaign,
  type TranscriptSessionItem,
  type TranscriptSkip,
  type TranscriptVersion,
  type TranscriptView,
  type FixableLine,
} from "./api";
import CampaignNav from "./CampaignNav";
import FixLine from "./FixLine";
import { FIX_LINE_LIVE } from "../content/fix-line-switch";

/** Where in the pages the address points: `/account/transcripts`, `/<campaign>`, `/<campaign>/<n>`. */
export function placeFromPath(path: string): { ref: string; number: number | null } | null {
  const match = /^\/account\/transcripts\/([^/]+)(?:\/(\d{1,6}))?\/?$/.exec(path);
  if (!match) return null;
  return {
    ref: decodeURIComponent(match[1] ?? ""),
    number: match[2] === undefined ? null : Number(match[2]),
  };
}

type Load<T> =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string }
  | { kind: "ready"; value: T };

/** One read from the API, with the states every page here shows. */
function useRead<T>(read: (() => Promise<T>) | null, deps: unknown[]): Load<T> {
  const [load, setLoad] = useState<Load<T>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setLoad({ kind: "loading" });
    if (!read) {
      setLoad({ kind: "error", message: text.failed });
      return;
    }
    read().then(
      (value) => live && setLoad({ kind: "ready", value }),
      (error: unknown) => {
        if (!live) return;
        const kind = error instanceof ApiError ? error.kind : "server";
        setLoad(
          kind === "signed-out"
            ? { kind: "signed-out" }
            : { kind: "error", message: problem(kind) },
        );
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return load;
}

function Shell<T>({
  load,
  children,
}: {
  load: Load<T>;
  children: (value: T) => preact.ComponentChildren;
}) {
  if (load.kind === "loading")
    return (
      <p class="muted" role="status">
        {text.loading}
      </p>
    );
  if (load.kind === "signed-out")
    return (
      <div class="panel" role="status">
        <p>{text.signedOut}</p>
      </div>
    );
  if (load.kind === "error")
    return (
      <p class="warn" role="alert">
        {load.message}
      </p>
    );
  return <>{children(load.value)}</>;
}

export default function Transcripts({ api, path }: { api: AccountApi; path: string }) {
  const place = placeFromPath(path);
  return (
    <div class="stack">
      <p>
        <a href="/account">{text.account}</a>
      </p>
      {place === null ? (
        <Campaigns api={api} />
      ) : place.number === null ? (
        <Sessions api={api} campaignRef={place.ref} />
      ) : (
        <Reader api={api} campaignRef={place.ref} number={place.number} />
      )}
    </div>
  );
}

function Campaigns({ api }: { api: AccountApi }) {
  const load = useRead(api.transcriptCampaigns ? () => api.transcriptCampaigns!() : null, [api]);
  return (
    <Shell load={load}>
      {(campaigns: TranscriptCampaign[]) =>
        campaigns.length === 0 ? (
          <p class="panel" role="status">
            {text.empty}
          </p>
        ) : (
          <>
            <p class="lead">{text.lead}</p>
            <ul class="campaigns">
              {campaigns.map((c) => (
                <li key={c.ref}>
                  <span class="campaign-name">{c.name}</span>
                  <span class="small">{text.campaignLine(c.serverName, c.sessions)}</span>
                  {text.keepLine(c.keepDays) && (
                    <span class="small">{text.keepLine(c.keepDays)}</span>
                  )}
                  <a
                    href={`/account/transcripts/${encodeURIComponent(c.ref)}`}
                    aria-label={text.openCampaign(c.name)}
                  >
                    {text.open}
                  </a>
                </li>
              ))}
            </ul>
          </>
        )
      }
    </Shell>
  );
}

function Sessions({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const load = useRead(
    api.transcriptSessions ? () => api.transcriptSessions!(campaignRef) : null,
    [api, campaignRef],
  );
  return (
    <Shell load={load}>
      {(found: { name: string; keepDays?: number | null; sessions: TranscriptSessionItem[] }) => (
        <>
          <p>
            <a href="/account/transcripts">{text.allCampaigns}</a>
          </p>
          <CampaignNav api={api} campaignRef={campaignRef} page="transcripts" name={found.name} />
          <h2>{found.name}</h2>
          <p class="small">{text.shareLine}</p>
          {IMPORT_LIVE && api.importPreview && (
            <p>
              <a href={`/account/import/${encodeURIComponent(campaignRef)}`}>{importPage.heading}</a>
            </p>
          )}
          {text.keepLine(found.keepDays) && (
            <p class="small">
              {text.keepLine(found.keepDays)}
              {found.keepDays ? ` ${text.keepSoon}` : ""}
            </p>
          )}
          {found.sessions.length === 0 ? (
            <p class="panel" role="status">
              {text.empty}
            </p>
          ) : (
            <ul class="campaigns">
              {found.sessions.map((s) => (
                <li key={s.number}>
                  <span class="campaign-name">{text.sessionTitle(s.number)}</span>
                  {s.transcriptsDeletedAt ? (
                    // Deleted: no line count and no Read link, since there is nothing to read.
                    <span class="small">
                      {sessionDay(s.startedAt)} · {text.deletedLine(sessionDay(s.transcriptsDeletedAt))}
                    </span>
                  ) : (
                    <>
                      <span class="small">{text.sessionLine(sessionDay(s.startedAt), s.lines)}</span>
                      <a
                        href={`/account/transcripts/${encodeURIComponent(campaignRef)}/${s.number}`}
                        aria-label={`${text.open}: ${text.sessionTitle(s.number)}`}
                      >
                        {text.open}
                      </a>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Shell>
  );
}

function Reader({
  api,
  campaignRef,
  number,
}: {
  api: AccountApi;
  campaignRef: string;
  number: number;
}) {
  const [version, setVersion] = useState<TranscriptVersion>("cleaned");
  const load = useRead(
    api.transcript ? () => api.transcript!(campaignRef, number, version) : null,
    [api, campaignRef, number, version],
  );
  return (
    <>
      <p>
        <a href={`/account/transcripts/${encodeURIComponent(campaignRef)}`}>
          {text.allSessions}
        </a>
      </p>
      <CampaignNav api={api} campaignRef={campaignRef} page="transcripts" />
      <h2>{text.sessionTitle(number)}</h2>
      {load.kind === "ready" && load.value.transcriptsDeletedAt ? (
        <p class="panel" role="status">
          {text.deletedPage(sessionDay(load.value.transcriptsDeletedAt))}
        </p>
      ) : (
        <Reading
          api={api}
          campaignRef={campaignRef}
          number={number}
          version={version}
          setVersion={setVersion}
          load={load}
        />
      )}
    </>
  );
}

/** What a session with a transcript shows: the two versions, the download, the lines. */
function Reading({
  api,
  campaignRef,
  number,
  version,
  setVersion,
  load,
}: {
  api: AccountApi;
  campaignRef: string;
  number: number;
  version: TranscriptVersion;
  setVersion: (v: TranscriptVersion) => void;
  load: Load<TranscriptView>;
}) {
  return (
    <>
      <div class="row" role="group" aria-label={text.versionLabel}>
        {(["cleaned", "heard"] as const).map((v) => (
          <button
            key={v}
            type="button"
            class={v === version ? "button" : "button secondary"}
            aria-pressed={v === version}
            onClick={() => setVersion(v)}
          >
            {text.versions[v]}
          </button>
        ))}
      </div>
      <p class="small">{text.difference[version]}</p>
      <p class="small">{text.shareLine}</p>
      <p class="small">{text.names}</p>
      <Shell load={load}>
        {(view: TranscriptView) => (
          <>
            <Download api={api} campaignRef={campaignRef} view={view} />
            <Lines key={`${version}-${number}`} api={api} campaignRef={campaignRef} view={view} />
            <details class="small muted">
              <summary>{text.about}</summary>
              <p>{versionNote[version]}</p>
              <p>{text.webNote}</p>
              {view.notes
                .filter((n) => n !== versionNote[version])
                .map((n, i) => (
                  <p key={i}>{n}</p>
                ))}
            </details>
          </>
        )}
      </Shell>
    </>
  );
}

function Download({
  api,
  campaignRef,
  view,
}: {
  api: AccountApi;
  campaignRef: string;
  view: TranscriptView;
}) {
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  const busy = useRef(false);
  if (!api.transcriptFile) return null;
  async function save() {
    if (busy.current) return;
    busy.current = true;
    setState("busy");
    try {
      const blob = await api.transcriptFile!(campaignRef, view.number, view.version);
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = view.fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
      setState("idle");
    } catch {
      setState("failed");
    } finally {
      busy.current = false;
    }
  }
  return (
    <p>
      <button type="button" class="button secondary" onClick={() => void save()}>
        {state === "busy" ? text.downloading : text.download}
      </button>{" "}
      <span role="status" class="warn">
        {state === "failed" ? text.downloadFailed : ""}
      </span>
    </p>
  );
}

/** Whether the signed-in person is one of the campaign's DMs, from their own campaigns list (it fails closed: a
 * missing answer or role is no DM). Only DMs are offered Put it back; the API refuses anyone else too. */
function useIsDm(api: AccountApi, campaignRef: string): boolean {
  const [dm, setDm] = useState(false);
  useEffect(() => {
    let live = true;
    void api
      .me()
      .then((me) => {
        const found = me?.campaigns.find((c) => c.ref === campaignRef);
        if (live) setDm(found?.role === "owner" || found?.role === "co-dm");
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, campaignRef]);
  return dm;
}

/** One stretch of off-topic chat the cleaned transcript left out (#2568). Anyone who may read the transcript
 * can open what was said, as text only; a DM can put it back (it joins the cleaned transcript and its downloads)
 * and leave it out again. The words are never HTML, and nothing is promised beyond what the API did. */
function LeftOut({
  api,
  campaignRef,
  number,
  skip,
  isDm,
}: {
  api: AccountApi;
  campaignRef: string;
  number: number;
  skip: TranscriptSkip;
  isDm: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [heard, setHeard] = useState<StretchLine[] | null>(null);
  const [back, setBack] = useState(skip.putBack);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const working = useRef(false);
  if (!api.transcriptStretch) return null;

  const fail = (error: unknown) => {
    const kind = error instanceof ApiError ? error.kind : "server";
    setProblem(offTopic.problem[kind] ?? offTopic.problem["server"] ?? "");
  };
  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (heard !== null || working.current) return;
    working.current = true;
    setProblem("");
    try {
      setHeard(await api.transcriptStretch!(campaignRef, number, skip));
    } catch (error) {
      fail(error);
      setOpen(false);
    } finally {
      working.current = false;
    }
  };
  const change = async (putBack: boolean) => {
    const call = putBack ? api.transcriptPutBack : api.transcriptLeaveOut;
    if (working.current || !call) return;
    working.current = true;
    setBusy(true);
    setProblem("");
    setNote("");
    try {
      const changed = await call(campaignRef, number, skip);
      setBack(putBack);
      setNote(
        changed === 0
          ? offTopic.nothingChanged
          : putBack
            ? offTopic.putBackDone + (PUT_BACK_READS_AGAIN ? offTopic.readsAgain : "")
            : offTopic.leftOutDone,
      );
    } catch (error) {
      fail(error);
    } finally {
      working.current = false;
      setBusy(false);
    }
  };

  return (
    <div class="stack left-out">
      <p class="small">{back ? offTopic.putBackLine(skip.lines) : offTopic.left(skip.lines, skip.seconds)}</p>
      <p>
        <button type="button" class="button secondary" aria-expanded={open} aria-label={offTopic.showFor(skip.lines)} onClick={() => void toggle()}>
          {open ? offTopic.hide : offTopic.show}
        </button>
      </p>
      {open && (
        <div class="panel stack" aria-busy={heard === null}>
          <p class="small muted">{heard === null ? offTopic.loading : offTopic.heading}</p>
          {heard !== null && (
            <ul class="transcript-lines">
              {heard.map((l, i) => (
                <li key={i}>
                  {offTopic.who(l.when, l.who)}: {l.text}
                </li>
              ))}
            </ul>
          )}
          {!isDm && heard !== null && <p class="small muted">{offTopic.onlyDm}</p>}
          {isDm && api.transcriptPutBack && api.transcriptLeaveOut && (
            <div class="stack">
              <p class="small">{offTopic.explainPutBack}</p>
              {back ? (
                <button type="button" class="button secondary" disabled={busy} aria-label={offTopic.leaveOutFor(skip.lines)} onClick={() => void change(false)}>
                  {busy ? offTopic.working : offTopic.leaveOut}
                </button>
              ) : (
                <button type="button" class="button" disabled={busy} aria-label={offTopic.putBackFor(skip.lines)} onClick={() => void change(true)}>
                  {busy ? offTopic.working : offTopic.putBack}
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {note && (
        <div role="status" class="small">
          <p>
            {note} {offTopic.reload}
          </p>
          <button type="button" class="button secondary" onClick={() => window.location.reload()}>
            {offTopic.reloadButton}
          </button>
        </div>
      )}
      {problem && (
        <p class="note bad" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

/** The moment asked for in the address, `#t=<milliseconds since the session began>` (#2402), or
 * null. Anything that is not a plain whole number is no moment. */
export function momentFromHash(hash: string): number | null {
  const match = /^#t=(\d{1,10})$/.exec(hash);
  return match ? Number(match[1]) : null;
}

/** Seconds since the session began, read from a line's own "[0:00:12]" start, or null for a line
 * with none (a note). */
export function lineOffsetSeconds(line: string): number | null {
  const match = /^\[(\d+):(\d{2}):(\d{2})\]/.exec(line);
  return match ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) : null;
}

/** The line that began at or just before the moment: the last one whose start is not after it. A
 * moment before the first line is the first line (such as `#t=0`). Null only when the loaded lines
 * may stop before the moment is reached (`more`) or none has a start: the caller loads the next page
 * and asks again. Lines that share a start (the clock counts whole seconds) are told apart by taking the
 * last of them: the moment is the end of that second, so the line that had begun by then is the
 * newest one. */
export function lineAtMoment(lines: string[], ms: number, more: boolean): number | null {
  let found: number | null = null;
  for (const [i, line] of lines.entries()) {
    const at = lineOffsetSeconds(line);
    if (at === null) continue;
    if (at * 1000 > ms) return found ?? i; // before the first line: the first line
    found = i;
  }
  return more ? null : found;
}

/** The transcript's lines, the first page the API gave and "Show more" for the next ones. Nothing
 * is said unless there is more. */
function Lines({
  api,
  campaignRef,
  view,
}: {
  api: AccountApi;
  campaignRef: string;
  view: TranscriptView;
}) {
  const [extra, setExtra] = useState<string[]>([]);
  // The left-out stretches of the pages loaded after the first, with the marker's place among all the lines.
  const [extraSkips, setExtraSkips] = useState<{ skip: TranscriptSkip; index: number }[]>([]);
  // The lines this viewer may fix, with the place each stands among all the lines (#2858), like the skips above.
  const [extraFix, setExtraFix] = useState<{ fix: FixableLine; index: number }[]>([]);
  const [fixBusy, setFixBusy] = useState(false);
  const [next, setNext] = useState<number | null>(view.next ?? null);
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  const busy = useRef(false);
  const lines = [...view.lines, ...extra];
  const skipAt = new Map<number, TranscriptSkip>([
    ...(view.skips ?? []).map((s): [number, TranscriptSkip] => [s.line, s]),
    ...extraSkips.map((e): [number, TranscriptSkip] => [e.index, e.skip]),
  ]);
  const fixAt = new Map<number, FixableLine>([
    ...(view.fixable ?? []).map((f): [number, FixableLine] => [f.line, f]),
    ...extraFix.map((e): [number, FixableLine] => [e.index, e.fix]),
  ]);
  const isDm = useIsDm(api, campaignRef);
  // Only the cleaned reading has fixes, and only when the switch is on and the API has the calls (#2858).
  const fixable = FIX_LINE_LIVE && view.version === "cleaned" && !!api.fixTranscriptLine && !!api.fixAnswer;
  async function more() {
    if (busy.current || next === null || !api.transcript) return;
    busy.current = true;
    setState("busy");
    try {
      const page = await api.transcript(campaignRef, view.number, view.version, next);
      setExtraSkips((before) => [...before, ...(page.skips ?? []).map((skip) => ({ skip, index: lines.length + skip.line }))]);
      setExtraFix((before) => [...before, ...(page.fixable ?? []).map((fix) => ({ fix, index: lines.length + fix.line }))]);
      setExtra((before) => [...before, ...page.lines]);
      setNext(page.next ?? null);
      setState("idle");
    } catch {
      setState("failed");
    } finally {
      busy.current = false;
    }
  }
  // A link can ask for a moment (#2402, the Check names card). Pages are loaded one after another until
  // the line is reached, then it is marked and scrolled into view; no moment behaves as before.
  // The address can change without the page reloading (a second moment link on the same page), so
  // the hash is state that follows `hashchange`.
  const [hash, setHash] = useState(() => (typeof window === "undefined" ? "" : window.location.hash));
  useEffect(() => {
    const follow = () => setHash(window.location.hash);
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);
  const moment = momentFromHash(hash);
  const marked = moment === null ? null : lineAtMoment(lines, moment, next !== null);
  const markedRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (moment !== null && marked === null && next !== null && state === "idle") void more();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moment, marked, next, state]);
  useEffect(() => {
    if (marked === null) return;
    markedRef.current?.scrollIntoView?.({ block: "center" });
    // Focus the line so a screen reader reads it: it has no other way to know where the page jumped.
    markedRef.current?.focus?.({ preventScroll: true });
  }, [marked, hash]);
  if (lines.length === 0)
    return (
      <p class="panel" role="status">
        {text.noLines}
      </p>
    );
  return (
    <>
      {view.filter && <p class="small muted">{offTopic.filter(view.filter.kept, view.filter.leftOut, view.filter.putBack)}</p>}
      <ul class="transcript-lines" aria-label={text.sessionTitle(view.number)}>
        {lines.map((line, i) => (
          <li
            key={i}
            {...(i === marked ? { ref: markedRef, class: "moment", tabIndex: -1, "aria-current": "location" as const } : {})}
          >
            {line}
            {skipAt.has(i) && <LeftOut api={api} campaignRef={campaignRef} number={view.number} skip={skipAt.get(i)!} isDm={isDm} />}
            {fixable && fixAt.has(i) && (
              <FixLine
                api={api}
                campaignRef={campaignRef}
                number={view.number}
                line={line}
                startedMs={fixAt.get(i)!.startedMs}
                speaker={fixAt.get(i)!.speaker}
                isDm={view.viewer?.isDm ?? isDm}
                busy={fixBusy}
                setBusy={setFixBusy}
              />
            )}
          </li>
        ))}
      </ul>
      {next !== null && (
        <p>
          <button type="button" class="button secondary" onClick={() => void more()}>
            {state === "busy" ? text.loadingMore : text.showMore}
          </button>{" "}
          <span role="status" class="warn">
            {state === "failed" ? text.moreFailed : ""}
          </span>
        </p>
      )}
    </>
  );
}
