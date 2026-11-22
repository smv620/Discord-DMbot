/** @jsxImportSource preact */
// The Maps page (#4347): the pictures a campaign keeps as maps. A DM answers yes or no to read a map, shows or hides it for the table, and
// removes it; the poster can take their own back. Every press is queued for the bot and is one change, so one Undo. A DM sees every map;
// a player only the maps shown to the table and their own (the API cuts the list, this page never filters by role). Words only, never
// a picture's contents. Held behind MAPS_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { mapsWords as w } from "../content/maps-words";
import { MAPS_LIVE } from "../content/maps-switch";
import { MAP_PINS_LIVE } from "../content/map-pins-switch";
import { mapPinsWords } from "../content/map-pins-words";
import { ApiError, type AccountApi, type MapItem, type MapsChange, type MapsRead } from "./api";
import LazyPanel from "./LazyPanel";
import MapAddForm from "./MapAddForm";
import WorkProgress from "./WorkProgress";
import CampaignNav from "./CampaignNav";

/** The campaign in the address: `/account/maps/<server>.<campaign>`. */
export function refFromPath(path: string): string | null {
  const match = /^\/account\/maps\/([^/]+?)\/?$/.exec(path);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1] ?? "");
  } catch {
    return null;
  }
}

/** A press still waiting after this long is said to be stuck, and what to do is said with it. */
const STUCK_SECONDS = 120;

type Said = { ok: boolean; text: string; batch: number | null; takeBack?: boolean };
type Asking = { id: string; kind: "show" | "remove" };

export default function Maps({ api, refId }: { api: AccountApi; refId: string }) {
  const [read, setRead] = useState<MapsRead | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [asking, setAsking] = useState<Asking | null>(null);
  const [pinsOpen, setPinsOpen] = useState<string | null>(null); // the map whose "Pins and scale" panel is open (#4672)
  const [since, setSince] = useState<number | null>(null);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const flight = useRef(false);
  // Counts the reads, so a slow older answer never covers a newer one.
  const reads = useRef(0);

  async function load(): Promise<void> {
    const mine = ++reads.current;
    try {
      const got = await api.maps!(refId);
      if (mine !== reads.current) return;
      setRead(got);
      setProblem(null);
    } catch {
      // A signed-out person sees the sign-in line the page's header already shows; the list just says it could not load.
      if (mine === reads.current) setProblem(w.failed);
    }
  }
  useEffect(() => {
    if (!MAPS_LIVE) return;
    if (refId) void load();
    else setProblem(w.failed);
  }, [api, refId]);
  // The clock only runs while a press waits.
  useEffect(() => {
    if (since === null) return;
    const tick = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(tick);
  }, [since]);

  /** `changedWords` is what a "changed" refusal means for this press: for an Undo, that the map was changed again; for any other press, that the map just changed. */
  async function press(change: MapsChange, then: (made: { batch: number | null }) => Said, changedWords: string = w.justChanged): Promise<void> {
    if (flight.current) return;
    if (!api.changeMaps) return setSaid({ ok: false, text: w.wrong, batch: null });
    flight.current = true;
    setAsking(null);
    setSaid(null);
    const start = Math.floor(Date.now() / 1000);
    setNow(start);
    setSince(start);
    try {
      setSaid(then(await api.changeMaps(refId, change)));
    } catch (error) {
      const kind = error instanceof ApiError ? error.kind : "";
      const text = kind === "gone" ? w.gone : kind === "not-allowed" ? w.dmOnly : kind === "changed" ? changedWords : w.wrong;
      setSaid({ ok: false, text, batch: null });
    } finally {
      flight.current = false;
      setSince(null);
      await load();
    }
  }

  const use = (m: MapItem, yes: boolean) => press({ action: "use", id: m.id, yes }, (r) => ({ ok: true, text: yes ? w.doneYes : w.doneNo, batch: r.batch }));
  const show = (m: MapItem, shown: boolean) => press({ action: "show", id: m.id, shown }, (r) => ({ ok: true, text: shown ? w.shownDone : w.hiddenDone, batch: r.batch }));
  const remove = (m: MapItem) => press({ action: "remove", id: m.id }, (r) => ({ ok: true, text: w.doneRemoved, batch: r.batch }));
  const takeBack = (m: MapItem) => press({ action: "takeback", id: m.id }, (r) => ({ ok: true, text: w.takenBack, batch: r.batch, takeBack: true }));
  // A take-back's Undo has its own words in Discord; any other Undo uses the general ones.
  const undo = (batch: number, wasTakeBack: boolean) =>
    press({ action: "undo", batch }, () => ({ ok: true, text: w.undone, batch: null }), wasTakeBack ? w.takeBackUndoFailed : w.undoFailed);

  if (!MAPS_LIVE) return <p class="muted">{w.notOpen}</p>;
  if (problem) {
    return (
      <section class="stack" aria-label={w.heading}>
        <p role="alert">{problem}</p>
        <button type="button" class="button secondary" onClick={() => void load()}>
          {w.tryAgain}
        </button>
      </section>
    );
  }
  if (!read) {
    return (
      <section class="stack" aria-label={w.heading}>
        <p class="muted" role="status">{w.loading}</p>
      </section>
    );
  }
  const busy = since !== null;
  return (
    <section class="stack" aria-label={w.heading} aria-busy={busy}>
      <CampaignNav api={api} campaignRef={refId} page="maps" name={null} />
      <p class="small">{w.lead}</p>
      <p class="small muted">{w.noCheck}</p>
      {busy && (
        <WorkProgress
          stage={w.workingStage}
          startedAt={since}
          serverNow={now}
          state={now - (since ?? now) >= STUCK_SECONDS ? "stalled" : "running"}
          whatToDo={w.stuck}
          progressUnknown
        />
      )}
      {read.maps.length === 0 && <p class="muted">{read.canManage ? w.emptyDm : w.emptyPlayer}</p>}
      <ul class="stack">
        {read.maps.map((m) => (
          <li key={m.id} class="stack">
            <h2 class="small">{m.title}</h2>
            <p class="small muted">{w.addedBy(m.addedBy, m.via)}</p>
            <p class="small">
              <a href={m.openUrl} target="_blank" rel="noopener noreferrer" aria-label={`${w.open}: ${m.title} (opens in a new tab)`}>{w.open}</a>
            </p>
            {MAP_PINS_LIVE && read.canManage && api.mapFeatures !== undefined && (
              <p class="small">
                <button
                  type="button"
                  class="button secondary"
                  aria-expanded={pinsOpen === m.id}
                  onClick={() => setPinsOpen(pinsOpen === m.id ? null : m.id)}
                >
                  {mapPinsWords.button}
                </button>
              </p>
            )}
            {MAP_PINS_LIVE && read.canManage && pinsOpen === m.id && (
              <LazyPanel load={() => import("./MapPins")} what="the pins and scale of this map" props={{ api, campaignRef: refId, map: m }} />
            )}
            {read.canManage && m.state === "waiting" && (
              <div class="stack">
                <p>{w.dmTitle(m.addedBy)}</p>
                <p class="row">
                  <button type="button" class="button" disabled={busy} onClick={() => void use(m, true)}>{w.useYes}</button>
                  <button type="button" class="button secondary" disabled={busy} onClick={() => void use(m, false)}>{w.useNo}</button>
                </p>
              </div>
            )}
            {read.canManage && m.state === "usable" && <p class="small">{w.usableNote}</p>}
            {m.mine && !read.canManage && m.state === "waiting" && <p class="small">{w.waitingPlayer}</p>}
            {m.mine && !read.canManage && m.state === "declined" && <p class="small">{w.declined}</p>}
            {read.canManage && m.state === "declined" && <p class="small">{w.declined}</p>}
            {read.canManage && m.state === "usable" && <p class="small">{m.shown ? w.shown : w.hidden}</p>}
            {asking?.id === m.id && (
              <div class="stack" role="group" aria-label={asking.kind === "show" ? w.showWarning : w.removeAsk}>
                <p>{asking.kind === "show" ? w.showWarning : w.removeAsk}</p>
                <p class="row">
                  <button type="button" class="button" disabled={busy} onClick={() => void (asking.kind === "show" ? show(m, true) : remove(m))}>
                    {asking.kind === "show" ? w.showIt : w.removeYes}
                  </button>
                  <button type="button" class="button secondary" onClick={() => setAsking(null)}>
                    {asking.kind === "show" ? w.cancel : w.keepIt}
                  </button>
                </p>
              </div>
            )}
            {asking?.id !== m.id && (
              <p class="row">
                {read.canManage && m.state === "usable" && (
                  m.shown ? (
                    <button type="button" class="button secondary" disabled={busy} onClick={() => void show(m, false)}>{w.hide}</button>
                  ) : (
                    <button type="button" class="button secondary" disabled={busy} onClick={() => setAsking({ id: m.id, kind: "show" })}>{w.show}</button>
                  )
                )}
                {read.canManage && (
                  <button type="button" class="button secondary" disabled={busy} onClick={() => setAsking({ id: m.id, kind: "remove" })}>{w.removeIt}</button>
                )}
                {m.mine && (
                  <button type="button" class="button secondary" disabled={busy} onClick={() => void takeBack(m)}>{w.takeBack}</button>
                )}
              </p>
            )}
          </li>
        ))}
      </ul>
      {api.addMap && <MapAddForm api={api} refId={refId} canManage={read.canManage} onAdded={() => void load()} />}
      {said && (
        <p class="row">
          <span class={said.ok ? "small" : "small warn"} role={said.ok ? "status" : "alert"}>{said.text}</span>
          {said.batch !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => void undo(said.batch!, said.takeBack === true)}>
              {w.undoLabel}
            </button>
          )}
        </p>
      )}
    </section>
  );
}
