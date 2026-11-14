/** @jsxImportSource preact */
// World notes on the Names page (#4285): DMbot's guesses about places and languages for the DM to check, the same list as Discord's
// 🌍 World notes. ✅ Keep one line or Keep all of a kind, 🚫 Not right on one, ↩ Undo of the last press. Every press is queued for the bot
// and is one change, so one Undo. Its own file, fetched only for a DM while WORLD_NOTES_LIVE. Words and counts only.
import { useEffect, useRef, useState } from "preact/hooks";

import { SHOWN_AT_MOST, worldNotesWords as w } from "../content/world-notes-words";
import { ApiError, type AccountApi, type WorldLanguageNote, type WorldNotes, type WorldPlaceNote } from "./api";

type Said = { ok: boolean; text: string; batch: number | null };

export default function WorldNotes({ api, refId, onSignedOut }: { api: AccountApi; refId: string; onSignedOut?: () => void }) {
  const [notes, setNotes] = useState<WorldNotes | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);

  async function load(): Promise<void> {
    try {
      setNotes(await api.worldNotes!(refId));
      setProblem(null);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      setProblem(error instanceof ApiError && error.kind === "not-allowed" ? w.notDm : w.failed);
    }
  }
  useEffect(() => {
    void load();
  }, [api, refId]);

  async function press(change: Parameters<NonNullable<AccountApi["changeWorldNotes"]>>[1], then: (made: { kept: number | null; batch: number | null }) => Said): Promise<void> {
    if (flight.current || !api.changeWorldNotes) return;
    flight.current = true;
    setBusy(true);
    setSaid(null);
    try {
      const made = await api.changeWorldNotes(refId, change);
      setSaid(then(made));
    } catch (error) {
      if (error instanceof ApiError && error.kind === "signed-out") return onSignedOut?.();
      // a line checked meanwhile: say so, and show the list as it is now
      setSaid({ ok: false, text: error instanceof ApiError && error.kind === "gone" ? w.gone : w.wrong, batch: null });
    } finally {
      flight.current = false;
      setBusy(false);
      await load();
    }
  }

  const keepOne = (line: string, id: string) => press({ action: "keep", ids: [id] }, (m) => ({ ok: true, text: w.kept(line), batch: m.batch }));
  const keepAll = (ids: string[]) => press({ action: "keep", ids }, (m) => ({ ok: true, text: w.keptMany(m.kept ?? ids.length), batch: m.batch }));
  const notRight = (line: string, id: string, kind: "place" | "language") =>
    press({ action: "not", id }, (m) => ({ ok: true, text: `${w.takenOut(line)} ${kind === "place" ? w.placeNever : w.languageAgain}`, batch: m.batch }));
  const undo = (batch: number) => press({ action: "undo", batch }, () => ({ ok: true, text: w.undone, batch: null }));

  if (problem) {
    return (
      <section class="stack" aria-label={w.heading}>
        <h2 class="small">{w.heading}</h2>
        <p role="alert">{problem}</p>
        {problem === w.failed && (
          <button type="button" class="button secondary" onClick={() => void load()}>
            {w.tryAgain}
          </button>
        )}
      </section>
    );
  }
  if (!notes) {
    return (
      <section class="stack" aria-label={w.heading}>
        <h2 class="small">{w.heading}</h2>
        <p class="muted" role="status">{w.loading}</p>
      </section>
    );
  }
  const total = notes.places.length + notes.languages.length;
  const group = (title: string, kind: "place" | "language", lines: (WorldPlaceNote | WorldLanguageNote)[], keepAllWords: (n: number) => string) => {
    if (lines.length === 0) return null;
    const shown = lines.slice(0, SHOWN_AT_MOST);
    const text = (n: WorldPlaceNote | WorldLanguageNote): string => (kind === "place" ? w.place(n as WorldPlaceNote) : w.language(n as WorldLanguageNote));
    const hint = (n: WorldPlaceNote | WorldLanguageNote): string =>
      `${w.kindHint[kind === "place" ? (n as WorldPlaceNote).layer : "language"] ?? ""} · ${n.from === "lore" ? w.fromLore : w.fromSession}`;
    return (
      <div class="stack" key={kind}>
        <h3 class="small">{title}</h3>
        <ul class="stack">
          {shown.map((n) => (
            <li key={n.id} class="stack">
              <p>{text(n)}</p>
              <p class="small muted">{hint(n)}</p>
              <p class="row">
                <button type="button" class="button secondary" disabled={busy} aria-label={w.keepLabel(text(n))} onClick={() => void keepOne(text(n), n.id)}>
                  {w.keep}
                </button>
                <button type="button" class="button secondary" disabled={busy} aria-label={w.notRightLabel(text(n))} onClick={() => void notRight(text(n), n.id, kind)}>
                  {w.notRight}
                </button>
              </p>
            </li>
          ))}
        </ul>
        {lines.length > shown.length && <p class="small muted">{w.showing(shown.length, lines.length)}</p>}
        <button type="button" class="button secondary" disabled={busy} onClick={() => void keepAll(shown.map((n) => n.id))}>
          {keepAllWords(shown.length)}
        </button>
      </div>
    );
  };
  return (
    <section class="stack" aria-label={w.heading} aria-busy={busy}>
      <h2 class="small">{w.heading}</h2>
      <p class="small">{w.intro}</p>
      {busy && <p class="small muted" role="status">{w.working}</p>}
      {total === 0 && <p class="muted">{w.none}</p>}
      {group(w.places, "place", notes.places, w.keepAllPlaces)}
      {group(w.languages, "language", notes.languages, w.keepAllLanguages)}
      {said && (
        <p class={said.ok ? "small" : "small warn"} role={said.ok ? "status" : "alert"}>
          {said.text}{" "}
          {said.batch !== null && (
            <button type="button" class="link" disabled={busy} onClick={() => void undo(said.batch!)}>
              {w.undo}
            </button>
          )}
        </p>
      )}
    </section>
  );
}
