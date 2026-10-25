/** @jsxImportSource preact */
// "Names fixed this session" (#2645), for a session's DM only: each name PlotBot's name pass wrote into
// the cleaned transcript, how many times, the spellings it replaced ("heard as"), and an Undo for each
// that goes through the bot like the DM screen's. The API refuses anyone else and then this renders
// nothing at all. The "heard as" spellings are transcript words: shown here and nowhere else, never put in
// a message, a log or a link. DMbot lists what it fixed; the DM decides whether it was right.
import { useEffect, useRef, useState } from "preact/hooks";

import { namesFixedWords as w } from "../content/names-fixed-words";
import { ApiError, type AccountApi, type NamesFixed as Fixed } from "./api";

// An unknown kind gets the general sentence, never the code itself.
const say = (error: unknown): string => {
  const kind = error instanceof ApiError ? error.kind : "server";
  return w.errors[kind] ?? (w.errors["server"] as string);
};

export default function NamesFixed({ api, recapRef }: { api: AccountApi; recapRef: string }) {
  const [got, setGot] = useState<Fixed | null>(null);
  const [hidden, setHidden] = useState(false);
  const [problem, setProblem] = useState(""); // the list could not be read
  const [undoProblem, setUndoProblem] = useState(""); // an Undo was refused: kept apart so the re-read can't wipe it
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState(""); // one polite place for results
  const [waited, setWaited] = useState(0); // seconds the Undo in progress has waited
  const latest = useRef(0); // the newest look's number: an older, slower answer is ignored
  const inFlight = useRef(false); // Undo goes one at a time, even two clicks in one tick
  const box = useRef<HTMLElement>(null);

  const load = async (): Promise<boolean> => {
    if (!api.namesFixed) return false;
    const mine = ++latest.current;
    try {
      const list = await api.namesFixed(recapRef);
      if (mine !== latest.current) return false;
      setGot(list);
      setProblem("");
      return true;
    } catch (error) {
      if (mine !== latest.current) return false;
      const kind = error instanceof ApiError ? error.kind : "";
      // A player, another campaign's DM or an unknown recap sees nothing at all.
      if (kind === "not-allowed" || kind === "not-found") setHidden(true);
      else setProblem(say(error));
      return false;
    }
  };

  useEffect(() => {
    // Another recap: nothing of the old one stays.
    latest.current += 1;
    setGot(null);
    setHidden(false);
    setProblem("");
    setUndoProblem("");
    setSaid("");
    void load();
    return () => {
      latest.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, recapRef]);

  // While an Undo waits, count the seconds so the DM can see it is still working (owner rule 2026-10-23).
  useEffect(() => {
    if (!busy) return;
    setWaited(0);
    const timer = setInterval(() => setWaited((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  // Nothing at all until the API has said this is a DM's list: no heading, no lead, for anyone else.
  if (hidden || !api.namesFixed || (!got && !problem)) return null;
  if (!got) {
    return (
      <p class="error" role="alert">
        {problem}
      </p>
    );
  }

  const undo = async (id: string): Promise<void> => {
    if (!api.undoNameFix || busy || inFlight.current) return;
    inFlight.current = true;
    setBusy(id);
    setUndoProblem("");
    setSaid("");
    let message = "";
    try {
      const done = await api.undoNameFix(recapRef, id);
      message = done.putBack > 0 ? w.putBack(done.putBack) : w.nothingBack;
    } catch (error) {
      setUndoProblem(say(error));
    } finally {
      // Read again either way, so the list matches the truth (it may have changed since the page read it).
      await load();
      inFlight.current = false;
      setBusy(null);
      if (message) setSaid(message);
      // The pressed button is gone once it worked (and was disabled while it waited): keep the keyboard on
      // that name's line, or on its button when the Undo was refused.
      setTimeout(() => {
        const at = box.current;
        (at?.querySelector<HTMLElement>(`[data-fix="${id}"]`) ?? at?.querySelector<HTMLElement>(`[data-row="${id}"]`))?.focus();
      }, 0);
    }
  };

  const check = w.stillChecking(got.unsure, got.new);
  return (
    <section class="stack" aria-labelledby="names-fixed-h" data-names-fixed ref={box}>
      <h2 id="names-fixed-h">{w.heading}</h2>
      <p class="small muted">{w.lead}</p>
      <p class="small" role="status" aria-live="polite">
        {said}
      </p>
      {(undoProblem || problem) && (
        <p class="error" role="alert">
          {undoProblem || problem}
        </p>
      )}
      {got.fixed.length === 0 ? (
        <p>{w.empty}</p>
      ) : (
        <ul class="stack">
          {got.fixed.map((f) => (
            <li key={f.id} class={f.undone ? "stack muted" : "stack"}>
              <p id={`fixed-${f.id}`} data-row={f.id} tabIndex={-1}>
                <strong>{f.name}</strong>: {w.times(f.times)}
              </p>
              <p class="small muted">
                {w.heardAs}: {f.forms.map((x) => w.heardTimes(x.heard, x.times)).join(", ")}
              </p>
              <div class="row" aria-busy={busy === f.id}>
                {f.undone ? (
                  <p class="small">{w.undone}</p>
                ) : (
                  <button
                    type="button"
                    class="button secondary"
                    disabled={!!busy}
                    data-fix={f.id}
                    aria-label={w.undoName(f.name)}
                    onClick={() => void undo(f.id)}
                  >
                    {w.undo}
                  </button>
                )}
                {busy === f.id && (
                  <span class="small muted">
                    {w.working(f.name, waited)} {waited >= 20 ? w.slow : ""}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {check && <p class="small">{check}</p>}
    </section>
  );
}
