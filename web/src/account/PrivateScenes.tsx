/** @jsxImportSource preact */
// "🤫 Private scenes" on the Names page (#4102, the web half of #2996 rule 5): the times two player characters were alone together while
// the party was split, found by code after each session. DM only. One action, Not private: DMbot never counts that scene again. The
// page shows names, the day, about how long and how many lines: never a word of what was said (none is kept) and never who spoke.
import { useEffect, useRef, useState } from "preact/hooks";

import { sceneWords as w } from "../content/private-scenes-words";
import { ApiError, type AccountApi, type PrivateScene } from "./api";
import SavingWait from "./SavingWait";

/** The session's day in the reader's own time zone, as Discord shows it. */
const dateOf = (at: number): string => new Date(at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

export default function PrivateScenes({ api, campaignRef }: { api: AccountApi; campaignRef: string }) {
  const [list, setList] = useState<PrivateScene[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [notYours, setNotYours] = useState(false);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const flight = useRef(false);
  const alive = useRef(true);
  const looks = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => void (alive.current = false);
  }, []);

  async function read(): Promise<void> {
    if (!api.privateScenes) return;
    const mine = (looks.current += 1);
    try {
      const got = await api.privateScenes(campaignRef);
      if (alive.current && mine === looks.current) (setList(got), setFailed(false));
    } catch (error) {
      if (!alive.current || mine !== looks.current) return;
      if (error instanceof ApiError && (error.kind === "not-allowed" || error.kind === "not-found")) setNotYours(true);
      else setFailed(true);
    }
  }
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, campaignRef]);

  const sayFailure = (error: unknown): string =>
    !(error instanceof ApiError) ? w.couldNot : error.kind === "gone" || error.kind === "not-found" ? w.gone : error.kind === "not-allowed" ? w.notAllowed : error.kind === "signed-out" ? w.signedOut : w.couldNot;

  async function notPrivate(scene: PrivateScene): Promise<void> {
    if (flight.current || !api.notPrivateScene) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      const names = await api.notPrivateScene(campaignRef, scene.id);
      if (alive.current) setSaid({ ok: true, text: w.done(names.first, names.second) });
    } catch (error) {
      if (alive.current) setSaid({ ok: false, text: sayFailure(error) });
    } finally {
      await read(); // (what shows is always the API's own)
      flight.current = false;
      if (alive.current) setBusy(false);
    }
  }

  if (!api.privateScenes || notYours) return null;
  if (failed && !list) return <p class="warn" role="alert">{w.failed}</p>;
  if (!list) return <p class="muted" role="status">{w.loading}</p>;
  return (
    <section class="panel stack" aria-labelledby="scenes-h">
      <h2 id="scenes-h">
        {w.heading}
        {list.length > 0 ? ` (${list.length})` : ""}
      </h2>
      <p class="small">{w.lead}</p>
      <p class={said?.ok ? "ok" : "visually-hidden"} role="status">
        {said?.ok ? said.text : ""}
      </p>
      <p class={said && !said.ok ? "warn" : "visually-hidden"} role="alert">
        {said && !said.ok ? said.text : ""}
      </p>
      {list.length === 0 && <p class="muted">{w.empty}</p>}
      <ul class="stack">
        {list.map((s) => (
          <li key={s.id} class="stack">
            <p id={`scene-${s.id}`}>
              <strong>{w.who(s.first, s.second)}</strong>
              <span class="small muted"> · {w.detail(dateOf(s.sessionStartedAt), s.minutes, s.lines)}</span>
            </p>
            <div class="row">
              <button type="button" class="button secondary" disabled={busy} aria-describedby={`scene-${s.id}`} onClick={() => void notPrivate(s)}>
                {w.notPrivate}
              </button>
            </div>
          </li>
        ))}
      </ul>
      <SavingWait active={busy} stage={w.saving} />
    </section>
  );
}
