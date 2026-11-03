/** @jsxImportSource preact */
// The kept pictures of one campaign (#3274, #3276): a grid of tiles, newest first, with filters and a search; a click
// opens the picture's own link in a new tab. A game master gets Permanent, Make private/public and Delete, and the
// usage line; the poster gets Make private/public and Delete on their own. Held behind PICTURES_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { sessionDay } from "../content/recaps";
import { picturesPage as words } from "../content/pictures-words";
import { PICTURES_LIVE } from "../content/pictures-switch";
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

type Tile = PictureItem & { working?: boolean; note?: string };
type Load = "loading" | "ok" | "failed";

export default function Pictures({ api, path }: { api: AccountApi; path: string }) {
  const ref = refFromPath(path);
  const [load, setLoad] = useState<Load>("loading");
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [more, setMore] = useState(false);
  const [moreBusy, setMoreBusy] = useState(false);
  const [isDm, setIsDm] = useState(false);
  const [usage, setUsage] = useState<PictureUsage | null>(null);
  const [sessions, setSessions] = useState<number[]>([]);
  const [posters, setPosters] = useState<string[]>([]);
  // The filters on screen, and the ones the list was last read with.
  const [session, setSession] = useState("");
  const [poster, setPoster] = useState("");
  const [typed, setTyped] = useState("");
  const [applied, setApplied] = useState<PictureFilter>({});
  const [busy, setBusy] = useState<string | null>(null);
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
    try {
      const got: PictureList = await api.pictures(ref, f);
      if (mine !== reads.current) return;
      setTiles(got.pictures);
      setMore(got.more);
      setIsDm(got.isDm);
      setUsage(got.usage);
      setSessions(got.sessions);
      setPosters(got.posters);
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
      const got = await api.pictures(ref, applied);
      setUsage(got.usage);
    } catch {
      // The usage line keeps what it said; the next read corrects it.
    }
  }

  useEffect(() => {
    if (!PICTURES_LIVE) return;
    void readFirst({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ref]);

  if (!PICTURES_LIVE || !api.pictures) return <p class="muted">{words.notOpen}</p>;

  function applyFilters(s: string, p: string, q: string): void {
    const f = filterFrom(s, p, q);
    setApplied(f);
    setConfirming(null);
    void readFirst(f);
  }

  async function showMore(): Promise<void> {
    const last = tiles[tiles.length - 1];
    if (!ref || !api.pictures || !last || moreBusy) return;
    setMoreBusy(true);
    try {
      const got = await api.pictures(ref, { ...applied, before: last.cursor });
      setTiles((old) => [...old, ...got.pictures]);
      setMore(got.more);
    } catch {
      setListProblem(true);
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

  async function act(tile: Tile, action: PictureAction): Promise<void> {
    if (!ref || !api.pictureAction || busy !== null) return;
    setBusy(tile.code);
    setProblem(tile.code, null);
    setConfirming(null);
    try {
      const result = await api.pictureAction(ref, tile.code, action);
      if (action === "delete" && result.state === "done") {
        setTiles((old) => old.filter((t) => t.code !== tile.code));
      } else if (result.state === "waiting") {
        // The bot still has it: the tile says so, and the list is asked again for what is really there.
        setTiles((old) => old.map((t) => (t.code === tile.code ? { ...t, working: true } : t)));
        void readFirst(applied);
      } else {
        setTiles((old) =>
          old.map((t) =>
            t.code !== tile.code
              ? t
              : {
                  ...t,
                  isPublic: action === "make-public" ? true : action === "make-private" ? false : t.isPublic,
                  permanent: action === "permanent" ? true : action === "not-permanent" ? false : t.permanent,
                  working: false,
                },
          ),
        );
        void refreshUsage();
      }
    } catch (error) {
      if (error instanceof ApiError && error.note) setProblem(tile.code, error.note);
      else if (error instanceof ApiError && error.kind === "permanent-full")
        setProblem(tile.code, words.permanentFull(usage?.permanentLimitMb ?? null));
      else if (error instanceof ApiError && error.kind === "not-allowed") setProblem(tile.code, words.notAllowed);
      else setProblem(tile.code, words.actionFailed);
    } finally {
      setBusy(null);
    }
  }

  const filtered = applied.session !== undefined || applied.poster !== undefined || applied.q !== undefined;

  return (
    <div class="stack">
      {ref && <CampaignNav api={api} campaignRef={ref} page="pictures" name={null} />}
      <p class="small muted">{words.intro}</p>
      {usage && isDm && (
        <p class="small" role="status">
          {words.usage(usage.usedMb, usage.limitMb, usage.permanentMb, usage.permanentLimitMb)}
        </p>
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
              applyFilters(v, poster, typed);
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
              applyFilters(session, v, typed);
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

      {load === "loading" && (
        <p class="muted" role="status">
          {words.loading}
        </p>
      )}
      {load === "failed" && (
        <div class="panel" role="alert">
          <p>{words.failed}</p>
          <button type="button" class="button secondary" onClick={() => void readFirst(applied)}>
            {words.tryAgain}
          </button>
        </div>
      )}
      {load === "ok" && tiles.length === 0 && <p class="muted">{filtered ? words.noMatch : words.none}</p>}

      {load === "ok" && tiles.length > 0 && (
        <ul class="campaigns" style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fill, minmax(12rem, 1fr))", listStyle: "none", padding: 0 }}>
          {tiles.map((tile) => {
            const canDm = isDm;
            const canPoster = tile.mine;
            const can = canDm || canPoster;
            const label = tile.description;
            return (
              <li key={tile.code} class="small">
                <a href={`/pictures/${encodeURIComponent(tile.code)}`} target="_blank" rel="noopener">
                  <img src={tile.previewUrl} alt={tile.description} style={{ width: "100%", height: "auto" }} />
                </a>
                <div>
                  <span class="muted">{sessionDay(tile.at)}</span>
                  {tile.sessionNumber !== null && <span class="muted"> · {words.sessionTag(tile.sessionNumber)}</span>}
                  <div>{tile.poster}</div>
                  {!tile.isPublic && <div class="tag">{words.privateTag}</div>}
                  {tile.permanent && <div class="tag">{words.permanentTag}</div>}
                  {tile.working && <p class="small muted" role="status">{words.working}</p>}
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
                      {canDm && (
                        <button
                          type="button"
                          class="button secondary"
                          aria-label={tile.permanent ? words.stopPermanent(label) : words.makePermanent(label)}
                          aria-pressed={tile.permanent}
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
