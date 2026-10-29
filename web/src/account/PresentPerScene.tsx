/** @jsxImportSource preact */
// "Who was in each scene" (#2577), under a recap: for each scene, the player characters DMbot thinks were
// there, and (for the DM, before the recap is posted) a way to change that list. The DM's word wins: this
// saves the whole list for a scene and DMbot never changes it afterwards. The list of characters to pick
// from comes with the recap (names only, DM's view only); a recap without scenes shows nothing at all.
import { useEffect, useRef, useState } from "preact/hooks";

import { presentWords as w } from "../content/present-words";
import { ApiError, type AccountApi, type Recap, type RecapBeat } from "./api";

const MAX_PRESENT = 12;

const say = (error: unknown): string => {
  const kind = error instanceof ApiError ? error.kind : "server";
  return w.errors[kind] ?? (w.errors["server"] as string);
};

export default function PresentPerScene({ api, recap, onRecap }: { api: AccountApi; recap: Recap; onRecap: (r: Recap) => void }) {
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const [said, setSaid] = useState("");
  const [updating, setUpdating] = useState(false); // the recap is being read again after a refusal
  const [slow, setSlow] = useState(false); // the save has taken longer than a few seconds
  const inFlight = useRef(false);
  const box = useRef<HTMLElement>(null);
  // A save that runs long says so, so the DM isn't left wondering (owner rule 2026-10-23).
  useEffect(() => {
    if (!saving) return setSlow(false);
    const timer = setTimeout(() => setSlow(true), 5000);
    return () => clearTimeout(timer);
  }, [saving]);
  // The newest recap the parent holds, so a save that finishes after something else changed it (a re-read, a
  // respelling) adds only its scene to that, not to the copy this render started with.
  const latest = useRef(recap);
  latest.current = recap;
  const beats = recap.beats ?? [];
  const characters = recap.characters ?? [];
  if (beats.length === 0) return null;
  const canChange = recap.canEdit && characters.length > 0 && !!api.setBeatPresent;

  const open = (b: RecapBeat): void => {
    setEditing(b.n);
    // Names DMbot listed that are no longer among the campaign's characters stay in the list (ticked), so
    // the DM sees them and can untick them; the save would refuse them (`bad-present`) if they stayed.
    setDraft([...b.present]);
    setProblem("");
    setSaid("");
    setTimeout(() => box.current?.querySelector<HTMLElement>(`[data-edit="${b.n}"] input`)?.focus(), 0);
  };
  const optionsFor = (b: RecapBeat): string[] => [...characters, ...b.present.filter((name) => !characters.includes(name))];
  const toggle = (name: string): void =>
    setDraft((d) => (d.includes(name) ? d.filter((x) => x !== name) : d.length < MAX_PRESENT ? [...d, name] : d));
  const back = (n: number): void => {
    setEditing(null);
    setTimeout(() => box.current?.querySelector<HTMLElement>(`[data-scene="${n}"]`)?.focus(), 0);
  };

  const save = async (n: number): Promise<void> => {
    if (!api.setBeatPresent || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    setProblem("");
    setSaid("");
    try {
      // In the campaign's order, whatever order they were ticked in, so the same choice is the same list.
      const here = beats.find((b) => b.n === n);
      const ordered = (here ? optionsFor(here) : characters).filter((name) => draft.includes(name));
      const got = await api.setBeatPresent(recap.ref, n, ordered);
      const now = latest.current;
      onRecap({ ...now, beats: (now.beats ?? []).map((b) => (b.n === n ? { n, present: got.present, saved: true } : b)) });
      setSaid(w.saved(n));
      back(n);
    } catch (error) {
      setProblem(say(error));
      // The recap changed (or can't be edited now): read it again so the scenes match the truth.
      const kind = error instanceof ApiError ? error.kind : "";
      if ((kind === "stale" || kind === "not-allowed" || kind === "not-found") && api.recap) {
        setUpdating(true);
        try {
          onRecap(await api.recap(recap.ref));
        } catch {
          // The scenes could not be read again: say so rather than claim they were updated.
          setProblem(w.errors["reread"] as string);
        } finally {
          setUpdating(false);
          back(n);
        }
      }
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };

  return (
    <section class="stack" aria-labelledby="present-h" data-present ref={box}>
      <h2 id="present-h">{w.heading}</h2>
      <p class="small muted">{w.lead}</p>
      {!recap.canEdit && <p class="small">{w.locked}</p>}
      <p class="small" role="status" aria-live="polite">
        {updating ? w.updating : said}
      </p>
      {problem && (
        <p class="error" role="alert">
          {problem}
        </p>
      )}
      <ul class="stack">
        {beats.map((b) => (
          <li key={b.n} class="stack">
            {editing === b.n ? (
              <fieldset class="stack" aria-busy={saving} data-edit={b.n}>
                <legend>{w.pick(b.n)}</legend>
                <p class="small muted">{draft.length >= MAX_PRESENT ? w.max : w.none}</p>
                <div class="row">
                  {optionsFor(b).map((name) => (
                    <label key={name}>
                      <input
                        type="checkbox"
                        checked={draft.includes(name)}
                        disabled={saving || (!draft.includes(name) && draft.length >= MAX_PRESENT)}
                        onChange={() => toggle(name)}
                      />{" "}
                      {name}
                    </label>
                  ))}
                </div>
                <div class="row">
                  <button type="button" class="button" disabled={saving} onClick={() => void save(b.n)}>
                    {saving ? w.saving : w.save}
                  </button>
                  <button type="button" class="button secondary" disabled={saving} onClick={() => back(b.n)}>
                    {w.cancel}
                  </button>
                </div>
                {slow && (
                  <p class="small muted" role="status">
                    {w.slow}
                  </p>
                )}
              </fieldset>
            ) : (
              <>
                <p tabIndex={-1} data-scene={b.n} id={`scene-${b.n}`}>
                  <strong>{w.scene(b.n)}</strong>: {b.present.length ? b.present.join(", ") : w.nobody}
                </p>
                {canChange && (
                  <div class="row">
                    <button
                      type="button"
                      class="button secondary"
                      disabled={editing !== null}
                      aria-label={w.changeScene(b.n)}
                      onClick={() => open(b)}
                    >
                      {w.change}
                    </button>
                  </div>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
