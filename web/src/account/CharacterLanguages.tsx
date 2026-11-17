/** @jsxImportSource preact */
// A character's languages on its row on the Names page (#4621, the web half of #4271): the DM sees them, adds one, marks the ones DMbot
// found as right, takes one away, and undoes the last change, level with Discord's 🗣️ Languages screen. Its own file, loaded only in the
// editor for a character while LANGUAGES_LIVE. Fetched only when Languages is pressed. Never shown to players. No id is shown.
import { useRef, useState } from "preact/hooks";

import { languagesWords as w } from "../content/languages-words";
import { ApiError, type AccountApi, type CharacterLanguages, type LanguagesChange } from "./api";

type Said = { ok: boolean; text: string };

export default function CharacterLanguages({ api, refId, row }: { api: AccountApi; refId: string; row: { id: string; name: string } }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<CharacterLanguages | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  const [undoBatch, setUndoBatch] = useState<number | null>(null);
  const [name, setName] = useState("");
  const flight = useRef(false);

  async function load(): Promise<void> {
    if (!api.characterLanguages) return;
    try {
      setData(await api.characterLanguages(refId, row.id));
      setProblem(null);
    } catch (error) {
      setProblem(error instanceof ApiError && error.kind === "not-allowed" ? w.notDm : w.wrong);
    }
  }

  // Fetched only when Languages is pressed.
  function toggle(): void {
    const next = !open;
    setOpen(next);
    if (next) void load();
  }

  async function press(change: LanguagesChange, then: string, undoable: boolean): Promise<void> {
    if (flight.current || !api.changeCharacterLanguages) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      const made = await api.changeCharacterLanguages(refId, row.id, change);
      setSaid({ ok: true, text: then });
      setUndoBatch(undoable && made.batch !== null ? made.batch : null);
      if (change.action === "add") setName("");
    } catch (error) {
      if (error instanceof ApiError && error.kind === "not-allowed") {
        setProblem(w.notDm);
      } else {
        setSaid({ ok: false, text: error instanceof ApiError && error.kind === "gone" ? w.gone : w.wrong });
        setUndoBatch(null);
      }
    } finally {
      flight.current = false;
      setBusy(false);
      await load();
    }
  }

  const add = (): void => {
    const text = name.trim();
    if (text === "" || text.length > 60) return;
    void press({ action: "add", name: text }, w.added(text), true);
  };
  const undo = (batch: number): void => {
    void press({ action: "undo", batch }, w.undone, false);
  };

  const hasFound = data?.languages.some((l) => l.source === "read") ?? false;

  return (
    <section class="stack" aria-label={w.heading}>
      <p class="row">
        <button type="button" class="button secondary" aria-expanded={open} onClick={toggle}>{w.heading}</button>
      </p>
      {open && problem && <p role="alert" class="warn">{problem}</p>}
      {open && !data && !problem && <p class="muted" role="status">{w.loading}</p>}
      {open && data && (
        <>
          {data.languages.length === 0 && <p class="muted">{w.none}</p>}
          {data.languages.length > 0 && (
            <ul class="stack">
              {data.languages.map((l, i) => (
                <li key={l.id ?? `s${i}`} class="row">
                  <span>
                    {l.name}
                    {l.source === "read" && w.notChecked}
                    {data.hasSheet && w.fromSheet}
                  </span>
                  {!data.hasSheet && l.id !== undefined && (
                    <button
                      type="button"
                      class="link"
                      disabled={busy}
                      aria-label={w.removeLabel(l.name)}
                      onClick={() => void press({ action: "remove", language: l.id! }, w.removed(l.name), true)}
                    >
                      {w.removeButton}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {data.hasSheet ? (
            <p class="small muted">{w.sheetSays}</p>
          ) : (
            <div class="stack">
              {hasFound && (
                <>
                  <p class="small muted">{w.checkLine}</p>
                  <p class="row">
                    <button type="button" class="button secondary" disabled={busy} onClick={() => void press({ action: "check" }, w.marked, true)}>
                      {w.checkButton}
                    </button>
                  </p>
                </>
              )}
              <p class="row">
                <label class="field">
                  {w.addLabel}
                  <input type="text" maxLength={60} value={name} disabled={busy} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
                </label>
                <button type="button" class="button secondary" disabled={busy || name.trim() === ""} onClick={add}>{w.addButton}</button>
              </p>
            </div>
          )}
        </>
      )}
      {busy && <p class="small muted" role="status">{w.working}</p>}
      {said && (
        <p class={said.ok ? "small" : "small warn"} role={said.ok ? "status" : "alert"}>
          {said.text}{" "}
          {undoBatch !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => undo(undoBatch)}>{w.undo}</button>
          )}
        </p>
      )}
    </section>
  );
}
