/** @jsxImportSource preact */
// The Story Graph's clean-up (#3740): the people and groups no chain of connections links to a player character, each
// with the scene DMbot kept, and the game master's Add a connection, plus, for a person the story never named, the Names
// page's own panel (give a name, same as someone DMbot knows, remove). Nothing is deleted by DMbot; a group leaves the
// list as soon as the page reads it again and finds it linked. Its own file, fetched only when a game master opens it.
import { useEffect, useRef, useState } from "preact/hooks";

import { cleanupWords as w } from "../content/graph-cleanup-words";
import { namePlaceWords } from "../content/name-places";
import { kindWords } from "../content/web-words";
import { ApiError, type AccountApi, type CleanupEntry, type CleanupView, type NameChange, type NamePlace, type WebNode } from "./api";
import NamePlaces from "./NamePlaces";
import { useObservedWait } from "./useObservedWait";
import WorkProgress from "./WorkProgress";
import LazyPanel from "./LazyPanel";

function asNode(e: CleanupEntry): WebNode {
  return { id: e.id, name: e.name, kind: e.kind, unnamed: true, ...(e.unnamedWhat ? { unnamedWhat: e.unnamedWhat } : {}), ...(e.unnamedAnchor !== undefined ? { unnamedAnchor: e.unnamedAnchor } : {}) };
}

/** "Where was it said?" for an entry that has no kept scene: the same lines view the name card has (NamePlaces), read for
 * this one entry only when the button is pressed, so a long list never makes a call for each entry. */
function WhereSaid({ entry, api, refId }: { entry: CleanupEntry; api: AccountApi; refId: string }) {
  const [state, setState] = useState<"idle" | "loading" | "none" | { places: NamePlace[]; more: number }>("idle");
  async function look(): Promise<void> {
    if (!api.cleanupPlaces || state === "loading") return;
    setState("loading");
    try {
      const got = await api.cleanupPlaces(refId, entry.id);
      setState(got.places.length === 0 && got.morePlaces <= 0 ? "none" : { places: got.places, more: got.morePlaces });
    } catch {
      setState("none");
    }
  }
  if (!api.cleanupPlaces) return null;
  return (
    <div class="stack">
      {state === "idle" && (
        <p>
          <button type="button" class="button secondary" aria-label={`${namePlaceWords.button}: ${entry.name}`} onClick={() => void look()}>
            <span aria-hidden="true">{namePlaceWords.buttonIcon}</span> {namePlaceWords.button}
          </button>
        </p>
      )}
      {state === "loading" && <p role="status">{w.lookingForLines}</p>}
      {state === "none" && <p class="small muted" role="status">{w.noLines}</p>}
      {typeof state === "object" && <NamePlaces places={state.places} more={state.more} campaignRef={refId} />}
    </div>
  );
}

/** Add a connection for one entry: find who it is connected to, say how, and send it (the same call the map's form makes). */
function AddConnection({ entry, api, refId, onAdded, onSaid, onSignedOut }: { entry: CleanupEntry; api: AccountApi; refId: string; onAdded: () => Promise<void>; onSaid: (text: string) => void; onSignedOut: () => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<{ id: string; name: string; kind: string }[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [target, setTarget] = useState<{ id: string; name: string } | null>(null);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const text = q.trim();
    if (text.length < 2 || !api.webFind) {
      latest.current += 1; // a reply for text that has since been cleared must not fill the list
      setSearching(false);
      return void setFound(null);
    }
    const mine = ++latest.current;
    setSearching(true);
    const timer = setTimeout(() => {
      api.webFind!(refId, { q: text }).then(
        (rows) => {
          if (mine !== latest.current) return;
          setFound(rows.filter((r) => r.id !== entry.id).slice(0, 8));
          setSearching(false);
        },
        () => {
          if (mine !== latest.current) return;
          setFound([]);
          setSearching(false);
        },
      );
    }, 250);
    return () => {
      clearTimeout(timer);
      setSearching(false);
    };
  }, [q]);

  async function add(e: Event): Promise<void> {
    e.preventDefault();
    if (busy) return;
    if (!target || words.trim() === "" || !api.changeWebLink) return setSaid({ ok: false, text: w.needBoth });
    setBusy(true);
    setSaid(null);
    try {
      await api.changeWebLink(refId, { action: "add", a: entry.id, b: target.id, words: words.trim(), secret: false });
      setSaid({ ok: true, text: w.added });
      onSaid(w.added);
      setTarget(null);
      setWords("");
      setQ("");
      await onAdded();
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut();
      setSaid({ ok: false, text: w.addFailed });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form class="stack" onSubmit={add} aria-label={w.addHeading(entry.name)}>
      <label class="field">
        {w.findLabel}
        <input type="text" value={q} onInput={(e) => setQ((e.target as HTMLInputElement).value)} aria-describedby={`find-hint-${entry.id}`} />
      </label>
      <p class="small muted" id={`find-hint-${entry.id}`}>
        {searching ? w.findWorking : found !== null && found.length === 0 ? w.findNone : w.findHint}
      </p>
      {found && found.length > 0 && (
        <ul class="stack">
          {found.map((r) => (
            <li key={r.id}>
              <button type="button" class="button secondary" aria-pressed={target?.id === r.id} aria-label={w.pick(r.name)} onClick={() => setTarget({ id: r.id, name: r.name })}>
                {r.name} <span class="tag">{kindWords(r.kind)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {target && <p class="small">{w.picked(target.name)}</p>}
      <label class="field">
        {w.wordsLabel}
        <input type="text" value={words} maxLength={100} onInput={(e) => setWords((e.target as HTMLInputElement).value)} aria-describedby={`words-hint-${entry.id}`} />
      </label>
      <p class="small muted" id={`words-hint-${entry.id}`}>
        {w.wordsHint}
      </p>
      <button type="submit" class="button" disabled={busy}>
        {w.add}
      </button>
      {busy && <p role="status">{w.adding}</p>}
      {said && (
        <p class={said.ok ? "ok" : "warn"} role={said.ok ? "status" : "alert"}>
          {said.text}
        </p>
      )}
    </form>
  );
}

export default function GraphCleanup({ api, refId, onSignedOut }: { api: AccountApi; refId: string; onSignedOut: () => void }) {
  const [view, setView] = useState<CleanupView | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<string | null>(null); // the entry whose actions are open
  const [naming, setNaming] = useState<CleanupEntry | null>(null);
  const [tick, setTick] = useState(0);
  // What the last change did, kept here because the form that made it leaves with a group that has just been linked.
  const [news, setNews] = useState("");
  const wait = useObservedWait(loading, String(tick));
  const reads = useRef(0); // an older, slower reply must not overwrite a newer list

  async function read(): Promise<void> {
    if (!api.graphCleanup) return;
    const mine = ++reads.current;
    setLoading(true);
    setFailed(false);
    try {
      const got = await api.graphCleanup(refId);
      if (mine === reads.current) setView(got);
    } catch (error) {
      if (mine !== reads.current) return;
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut();
      setFailed(true);
    } finally {
      if (mine === reads.current) setLoading(false);
    }
  }
  useEffect(() => {
    void read();
  }, [refId, tick]);

  const shown = view ? view.groups.reduce((n, g) => n + g.entries.length, 0) : 0;
  // (`ok` is part of the shape the panel is given; the panel says what happened itself, so it is not used here)
  const act = async (id: string, change: NameChange, _ok: string): Promise<string | null> => {
    if (!api.changeName) return w.changeFailed;
    try {
      await api.changeName(refId, id, change);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") {
        onSignedOut();
        return ""; // (the panel treats an empty problem as "nothing to say": the page is already on the sign-in screen)
      }
      return w.changeFailed;
    }
    await read();
    return null;
  };

  return (
    <section class="panel stack" aria-labelledby="cleanup-h">
      <h2 id="cleanup-h">{w.heading}</h2>
      <p class="small muted">{w.lead}</p>
      <p class={news ? "ok" : "visually-hidden"} role="status">
        {news}
      </p>
      {loading && <WorkProgress stage={w.stage} startedAt={wait.startedAt} updatedAt={wait.movedAt} state={wait.quiet ? "stalled" : "running"} progressUnknown whatToDo={w.quiet} />}
      {failed && !loading && (
        <div class="stack">
          <p class="warn" role="alert">
            {w.down}
          </p>
          <button type="button" class="button" onClick={() => setTick((n) => n + 1)}>
            {w.tryAgain}
          </button>
        </div>
      )}
      {view && !failed && (
        <>
          {view.review && view.review.state === "waiting" && <p class="small" role="status">{w.reviewWaiting(view.review.minutesLeft ?? 15, view.review.fixes ?? 0)}</p>}
          {view.review && view.review.state === "running" && <p class="small" role="status">{w.reviewRunning}</p>}
          {view.review && view.review.state === "done" && <p class="small" role="status">{w.reviewDone(view.review.missed ?? 0)}</p>}
          {view.groups.length === 0 ? (
            <p role="status">{view.noPlayerCharacter === true ? w.noPlayerCharacter : view.noPlayerCharacter === false ? w.nothing : w.nothingListed}</p>
          ) : (
            <>
              <p role="status">
                <strong>{w.count(view.total)}</strong>
              </p>
              {view.truncated && <p class="small">{w.showing(shown, view.total)}</p>}
              <ol class="stack">
                {view.groups.map((g, n) => (
                  <li key={g.entries[0]?.id ?? n} class="stack">
                    <h3 class="small">{w.group}</h3>
                    <ul class="stack">
                      {g.entries.map((e) => (
                        <li key={e.id} class="stack">
                          <p>
                            <strong>{e.name}</strong> <span class="tag">{kindWords(e.kind)}</span>
                            {e.waiting && <span class="small muted"> ({w.waiting})</span>}
                          </p>
                          <p class="small">{e.scene ? `${w.scene} ${e.scene}` : w.noScene}</p>
                          {!e.scene && <WhereSaid entry={e} api={api} refId={refId} />}
                          <p>
                            <button type="button" class="button secondary" aria-expanded={open === e.id} aria-label={open === e.id ? `${w.done}: ${e.name}` : w.addHeading(e.name)} onClick={() => setOpen(open === e.id ? null : e.id)}>
                              {open === e.id ? w.done : w.addShort}
                            </button>
                            {/* The Names page reads `#said=<the name>` (a fragment never leaves the browser, so a secret name is safe) and opens that name's editor, where Same as… and Remove are. */}
                            {!e.unnamed && (
                              <a class="button secondary" href={`/account/names/${encodeURIComponent(refId)}#said=${encodeURIComponent(e.name)}`} aria-label={`${w.namesPage}: ${e.name}`}>
                                {w.namesPage}
                              </a>
                            )}
                            {e.unnamed && api.nameThem && (
                              <button type="button" class="button secondary" aria-expanded={naming?.id === e.id} aria-label={naming?.id === e.id ? `${w.done}: ${e.name}` : `${w.nameThem}: ${e.name}`} onClick={() => setNaming(naming?.id === e.id ? null : e)}>
                                {naming?.id === e.id ? w.done : w.nameThem}
                              </button>
                            )}
                          </p>
                          {open === e.id && <AddConnection entry={e} api={api} refId={refId} onAdded={read} onSaid={setNews} onSignedOut={onSignedOut} />}
                        </li>
                      ))}
                    </ul>
                    {g.links.length > 0 && (
                      <>
                        <h4 class="small">{w.linksHeading}</h4>
                        <ul>
                          {g.links.map((edge) => (
                            <li key={`${edge.a}-${edge.b}`} class="small">
                              {g.entries.find((x) => x.id === edge.a)?.name ?? w.someone} — {edge.label} — {g.entries.find((x) => x.id === edge.b)?.name ?? w.someone}
                              {edge.more > 0 && ` (+${edge.more})`}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </li>
                ))}
              </ol>
            </>
          )}
          {naming && (
            <LazyPanel
              key={naming.id}
              load={() => import("./WebUnnamed")}
              what="the name editor"
              props={{ node: asNode(naming), api, refId, act: (change: NameChange, ok: string) => act(naming.id, change, ok), reload: read, onSignedOut, onClose: () => setNaming(null) }}
            />
          )}
          <p>
            <button type="button" class="button secondary" disabled={loading} onClick={() => setTick((n) => n + 1)}>
              {w.refresh}
            </button>
          </p>
        </>
      )}
    </section>
  );
}
