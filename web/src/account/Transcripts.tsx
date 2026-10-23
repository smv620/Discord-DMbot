/** @jsxImportSource preact */
// The session transcripts (#1501): every campaign with a saved transcript in the person's servers,
// a campaign's sessions, and one session to read in two versions with a download. Anyone in the
// Discord server may read them (CLAUDE.md), so this is for players as much as DMs; the API decides
// who sees what, and the text is the bot's own export, so nothing is formatted here.
import { useEffect, useRef, useState } from "preact/hooks";

import { IMPORT_LIVE, importPage } from "../content/import-words";
import { sessionDay } from "../content/recaps";
import { problem, text, versionNote } from "../content/transcripts";
import {
  ApiError,
  type AccountApi,
  type TranscriptCampaign,
  type TranscriptSessionItem,
  type TranscriptVersion,
  type TranscriptView,
} from "./api";
import CampaignNav from "./CampaignNav";

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

/** The transcript's lines, the first page the API gave and "Show more" for the next ones. Nothing
 * is said unless there is more. */
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
 * and asks again. */
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
  const [next, setNext] = useState<number | null>(view.next ?? null);
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  const busy = useRef(false);
  const lines = [...view.lines, ...extra];
  async function more() {
    if (busy.current || next === null || !api.transcript) return;
    busy.current = true;
    setState("busy");
    try {
      const page = await api.transcript(campaignRef, view.number, view.version, next);
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
  const moment = typeof window === "undefined" ? null : momentFromHash(window.location.hash);
  const marked = moment === null ? null : lineAtMoment(lines, moment, next !== null);
  const markedRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (moment !== null && marked === null && next !== null && state === "idle") void more();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moment, marked, next, state]);
  useEffect(() => {
    if (marked !== null) markedRef.current?.scrollIntoView?.({ block: "center" });
  }, [marked]);
  if (lines.length === 0)
    return (
      <p class="panel" role="status">
        {text.noLines}
      </p>
    );
  return (
    <>
      <ul class="transcript-lines" aria-label={text.sessionTitle(view.number)}>
        {lines.map((line, i) => (
          <li
            key={i}
            {...(i === marked ? { ref: markedRef, class: "moment", "aria-current": "location" as const } : {})}
          >
            {line}
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
