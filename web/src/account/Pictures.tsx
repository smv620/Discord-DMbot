/** @jsxImportSource preact */
// The kept pictures of one campaign (#3274, #3276): a grid of tiles, newest first, with filters and a search; a click
// opens the picture's own link in a new tab. A game master gets Permanent, Make private/public and Delete, and the
// usage line; the poster gets Make private/public and Delete on their own. Held behind PICTURES_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { picturesPage as words } from "../content/pictures-words";
import { PICTURES_LIVE } from "../content/pictures-switch";
import { sessionDay } from "../content/recaps";
import {
  ApiError,
  type AccountApi,
  type PictureAction,
  type PictureFilter,
  type PictureItem,
  type PictureList,
  type PictureUsage,
} from "./api";
import CampaignNav from "./CampaignNav";
import WorkProgress from "./WorkProgress";

/** The campaign in the address: `/account/pictures/<server>.<campaign>` (the page's own link, as Timeline does). */
export function refFromPath(path: string): string | null {
  const match = /^\/account\/pictures\/([^/]+?)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null;
  }
}

type Load = "loading" | "ok" | "failed";

/** After a "waiting" answer the list is read once more after this long: the bot usually answers within a minute, and
 * the page should notice by itself rather than wait for a reload (#3322). */
export const WAITING_RECHECK_MS = 10_000;

/** A picture action still waiting after this long is said to be stuck, and what to do is said with it (#3347). */
const STUCK_SECONDS = 120;

/** Has the list now shown what an action asked for? (A deleted tile is gone; the others carry the new flag.) */
function shows(tiles: PictureItem[], code: string, action: PictureAction): boolean {
  const tile = tiles.find((t) => t.code === code);
  if (action === "delete") return tile === undefined;
  if (tile === undefined) return true;
  if (action === "make-private") return !tile.isPublic;
  if (action === "make-public") return tile.isPublic;
  if (action === "permanent") return tile.permanent;
  return !tile.permanent;
}

export default function Pictures({ api, path }: { api: AccountApi; path: string }) {
  const ref = refFromPath(path);
  const [load, setLoad] = useState<Load>("loading");
  const [tiles, setTiles] = useState<PictureItem[]>([]);
  const [more, setMore] = useState(false);
  const [moreBusy, setMoreBusy] = useState(false);
  const [isDm, setIsDm] = useState(false);
  const [usage, setUsage] = useState<PictureUsage | null>(null);
  const [sessions, setSessions] = useState<number[]>([]);
  const [posters, setPosters] = useState<string[]>([]);
  // The filters on screen, and the ones the list was last read with (kept in a ref too, for a read that starts later).
  const [session, setSession] = useState("");
  const [poster, setPoster] = useState("");
  const [typed, setTyped] = useState("");
  const [applied, setApplied] = useState<PictureFilter>({});
  const appliedRef = useRef<PictureFilter>({});
  // An action waiting for the bot: its picture's code and the action, until the list shows it.
  const [waiting, setWaiting] = useState<Record<string, PictureAction>>({});
  // The same waiting actions, readable from a timer (#3322).
  const waitingNow = useRef<Record<string, PictureAction>>({});
  // When each waiting tile began to wait (page clock, seconds): kept with the waiting state, so the wait can say how long (#3347).
  const waitingSince = useRef<Record<string, number>>({});
  const [since, setSince] = useState<Record<string, number>>({});
  // One rhythm for every waiting tile: the list is read again every WAITING_RECHECK_MS while any tile waits (#3322).
  const rhythm = useRef<ReturnType<typeof setInterval> | null>(null);
  // False once the page is left: an action still in flight then must not schedule anything (#3322).
  const mounted = useRef(true);
  function setWaitingState(next: Record<string, PictureAction>): void {
    waitingNow.current = next;
    // A tile that was already waiting keeps its start time; a new one starts now.
    const kept: Record<string, number> = {};
    for (const code of Object.keys(next)) kept[code] = waitingSince.current[code] ?? Math.floor(Date.now() / 1000);
    waitingSince.current = kept;
    setSince(kept);
    setWaiting(next);
    if (Object.keys(next).length === 0) stopRhythm();
    else startRhythm();
  }
  function stopRhythm(): void {
    if (rhythm.current !== null) clearInterval(rhythm.current);
    rhythm.current = null;
  }
  function startRhythm(): void {
    if (rhythm.current !== null) return;
    rhythm.current = setInterval(() => {
      if (!mounted.current || Object.keys(waitingNow.current).length === 0) return stopRhythm();
      if (typeof document !== "undefined" && document.hidden) return; // a hidden tab does not read
      void readFirst(appliedRef.current);
    }, WAITING_RECHECK_MS);
  }
  /** Has this tile been waiting for STUCK_SECONDS or more, by the page's clock? */
  const stalledNow = (code: string): boolean => {
    const began = since[code];
    return began !== undefined && Math.floor(Date.now() / 1000) - began >= STUCK_SECONDS;
  };
  const [busy, setBusy] = useState<string | null>(null);
  const busyRef = useRef(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [listProblem, setListProblem] = useState(false);
  // Each read gets a number; an answer to an older read (a filter changed meanwhile) is dropped.
  const reads = useRef(0);

  function filterFrom(s: string, p: string, q: string): PictureFilter {
    const f: PictureFilter = {};
    if (s !== "") f.session = Number(s);
    if (p !== "") f.poster = p;
    if (q.trim() !== "") f.q = q.trim();
    return f;
  }

  async function readFirst(f: PictureFilter): Promise<void> {
    if (!ref || !api.pictures) return setLoad("failed");
    const mine = ++reads.current;
    setLoad("loading");
    setListProblem(false);
    setProblems({});
    try {
      const got: PictureList = await api.pictures(ref, f);
      if (mine !== reads.current) return;
      setTiles(got.pictures);
      setMore(got.more);
      setIsDm(got.isDm);
      setUsage(got.usage);
      setSessions(got.sessions);
      setPosters(got.posters);
      // A waiting action is done once the list shows it (or its picture is gone); until then it stays on its tile.
      const next: Record<string, PictureAction> = {};
      for (const [code, action] of Object.entries(waitingNow.current))
        if (!shows(got.pictures, code, action)) next[code] = action;
      setWaitingState(next);
      setLoad("ok");
    } catch {
      if (mine !== reads.current) return;
      setLoad("failed");
    }
  }

  // The usage line only: asked again after a change, with the filters the list is showing.
  async function refreshUsage(): Promise<void> {
    if (!ref || !api.pictures) return;
    try {
      const got = await api.pictures(ref, appliedRef.current);
      setUsage(got.usage);
    } catch {
      // The usage line keeps what it said; the next read corrects it.
    }
  }

  useEffect(() => {
    if (!PICTURES_LIVE) return;
    void readFirst({});
  }, [api, ref]);

  // Leaving the page drops the rhythm, so nothing reads after the page is gone.
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stopRhythm();
    };
  }, []);
  // A different campaign's list must not be read by a rhythm started for this one.
  useEffect(() => stopRhythm, [api, ref]);

  if (!PICTURES_LIVE || !api.pictures) return <p class="muted">{words.notOpen}</p>;

  function applyFilters(s: string, p: string, q: string): void {
    const f = filterFrom(s, p, q);
    appliedRef.current = f;
    setApplied(f);
    setConfirming(null);
    void readFirst(f);
  }

  async function showMore(): Promise<void> {
    const last = tiles[tiles.length - 1];
    if (!ref || !api.pictures || !last || moreBusy) return;
    const mine = reads.current;
    setMoreBusy(true);
    try {
      const got = await api.pictures(ref, { ...appliedRef.current, before: last.cursor });
      if (mine !== reads.current) return; // the filter changed while this page was on its way
      setTiles((old) => [...old, ...got.pictures]);
      setMore(got.more);
    } catch {
      if (mine === reads.current) setListProblem(true);
    } finally {
      setMoreBusy(false);
    }
  }

  function setProblem(code: string, text: string | null): void {
    setProblems((old) => {
      const next = { ...old };
      if (text === null) delete next[code];
      else next[code] = text;
      return next;
    });
  }

  async function act(tile: PictureItem, action: PictureAction): Promise<void> {
    // A ref, not the render's `busy`: two quick presses before the next render must not start two actions.
    if (!ref || !api.pictureAction || busyRef.current) return;
    busyRef.current = true;
    setBusy(tile.code);
    setProblem(tile.code, null);
    setConfirming(null);
    try {
      const result = await api.pictureAction(ref, tile.code, action);
      if (!mounted.current) return; // the page was left while the action was on its way
      if (result.state === "waiting") {
        setWaitingState({ ...waitingNow.current, [tile.code]: action });
        void readFirst(appliedRef.current);
      } else if (action === "delete") {
        setTiles((old) => old.filter((t) => t.code !== tile.code));
        void refreshUsage();
      } else {
        setTiles((old) =>
          old.map((t) =>
            t.code !== tile.code
              ? t
              : {
                  ...t,
                  isPublic: action === "make-public" ? true : action === "make-private" ? false : t.isPublic,
                  permanent: action === "permanent" ? true : action === "not-permanent" ? false : t.permanent,
                },
          ),
        );
        void refreshUsage();
      }
    } catch (error) {
      if (error instanceof ApiError && error.note) setProblem(tile.code, error.note);
      else if (error instanceof ApiError && error.kind === "permanent-full")
        setProblem(tile.code, words.permanentFull(usage?.permanentLimitMb ?? null));
      else setProblem(tile.code, words.actionFailed);
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }

  const filtered = applied.session !== undefined || applied.poster !== undefined || applied.q !== undefined;

  return (
    <div class="stack">
      {ref && <CampaignNav api={api} campaignRef={ref} page="pictures" name={null} />}
      <p class="small muted">{words.intro}</p>
      {usage && isDm && (
        <>
          <p class="small" role="status">
            {words.usage(usage.usedMb, usage.limitMb, usage.permanentMb, usage.permanentLimitMb)}
          </p>
          <p class="small muted">{words.roomNote}</p>
        </>
      )}
      <form
        class="row"
        onSubmit={(e) => {
          e.preventDefault();
          applyFilters(session, poster, typed);
        }}
      >
        <label class="small">
          {words.sessionLabel}{" "}
          <select
            value={session}
            onChange={(e) => {
              const v = (e.currentTarget as HTMLSelectElement).value;
              setSession(v);
              // A selection applies the filters already set; text typed in the search box waits for its button.
              applyFilters(v, poster, applied.q ?? "");
            }}
          >
            <option value="">{words.allSessions}</option>
            {sessions.map((n) => (
              <option key={n} value={String(n)}>
                {words.sessionOption(n)}
              </option>
            ))}
          </select>
        </label>
        <label class="small">
          {words.posterLabel}{" "}
          <select
            value={poster}
            onChange={(e) => {
              const v = (e.currentTarget as HTMLSelectElement).value;
              setPoster(v);
              applyFilters(session, v, applied.q ?? "");
            }}
          >
            <option value="">{words.everyone}</option>
            {posters.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label class="small">
          {words.searchLabel}{" "}
          <input type="search" value={typed} onInput={(e) => setTyped((e.currentTarget as HTMLInputElement).value)} />
        </label>
        <button type="submit" class="button secondary">
          {words.search}
        </button>
      </form>

      {load === "loading" && tiles.length === 0 && (
        <p class="muted" role="status">
          {words.loading}
        </p>
      )}
      {load === "failed" && (
        <div class="panel" role="alert">
          <p>{words.failed}</p>
          <button type="button" class="button secondary" onClick={() => void readFirst(appliedRef.current)}>
            {words.tryAgain}
          </button>
        </div>
      )}
      {load === "ok" && tiles.length === 0 && <p class="muted">{filtered ? words.noMatch : words.none}</p>}

      {tiles.length > 0 && (
        <ul class="picture-grid">
          {tiles.map((tile) => {
            const can = isDm || tile.mine;
            return (
              <li key={tile.code} class="small">
                <a href={`/pictures/${encodeURIComponent(tile.code)}`} target="_blank" rel="noopener">
                  <img src={tile.previewUrl} alt={tile.description} />
                </a>
                <div>
                  <span class="muted">{sessionDay(tile.at)}</span>
                  {tile.sessionNumber !== null && <span class="muted"> · {words.sessionTag(tile.sessionNumber)}</span>}
                  <div>{tile.poster}</div>
                  {!tile.isPublic && <div class="tag">{words.privateTag}</div>}
                  {tile.permanent && <div class="tag">{words.permanentTag}</div>}
                  {waiting[tile.code] && (
                    <WorkProgress
                      stage={words.waitingStage}
                      startedAt={since[tile.code] ?? Math.floor(Date.now() / 1000)}
                      state={stalledNow(tile.code) ? "stalled" : "running"}
                      whatToDo={stalledNow(tile.code) ? words.stuck : undefined}
                      progressUnknown
                    />
                  )}
                  {problems[tile.code] && (
                    <p class="note bad" role="alert">
                      {problems[tile.code]}
                    </p>
                  )}
                </div>
                {can && confirming === tile.code ? (
                  <div class="row">
                    <p>{words.confirmDelete}</p>
                    <button type="button" class="button" disabled={busy !== null} onClick={() => void act(tile, "delete")}>
                      {words.confirmRemove}
                    </button>
                    <button type="button" class="button secondary" onClick={() => setConfirming(null)}>
                      {words.cancel}
                    </button>
                  </div>
                ) : (
                  can && (
                    <div class="row">
                      {isDm && (
                        <button
                          type="button"
                          class="button secondary"
                          aria-label={tile.permanent ? words.stopPermanent(tile.description) : words.makePermanent(tile.description)}
                          disabled={busy !== null}
                          onClick={() => void act(tile, tile.permanent ? "not-permanent" : "permanent")}
                        >
                          {tile.permanent ? words.notPermanent : words.permanent}
                        </button>
                      )}
                      <button
                        type="button"
                        class="button secondary"
                        disabled={busy !== null}
                        onClick={() => void act(tile, tile.isPublic ? "make-private" : "make-public")}
                      >
                        {tile.isPublic ? words.makePrivate : words.makePublic}
                      </button>
                      <button type="button" class="button secondary" disabled={busy !== null} onClick={() => setConfirming(tile.code)}>
                        {words.remove}
                      </button>
                    </div>
                  )
                )}
              </li>
            );
          })}
        </ul>
      )}
      {listProblem && (
        <p class="note bad" role="alert">
          {words.failed}
        </p>
      )}
      {load === "ok" && more && (
        <button type="button" class="button secondary" disabled={moreBusy} onClick={() => void showMore()}>
          {words.showMore}
        </button>
      )}
    </div>
  );
}
