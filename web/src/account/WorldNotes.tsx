/** @jsxImportSource preact */
// World notes on the Names page (#4285): DMbot's guesses about places and languages for the DM to check, the same list as Discord's
// 🌍 World notes. ✅ Keep one line or Keep all of a kind, 🚫 Not right on one, ↩ Undo of the last press. Every press is queued for the bot
// and is one change, so one Undo. Its own file, fetched only for a DM while WORLD_NOTES_LIVE. Words and counts only.
import { useEffect, useRef, useState } from "preact/hooks";

import { ROADS_LIVE } from "../content/roads-switch";
import { WORLD_FEELINGS_LIVE } from "../content/world-feelings-switch";
import { SHOWN_AT_MOST, worldNotesWords as w } from "../content/world-notes-words";
import { ApiError, type AccountApi, type WorldFeelingNote, type WorldLanguageNote, type WorldNotes, type WorldPlaceNote, type WorldRoadNote } from "./api";

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
      const kind = error instanceof ApiError ? error.kind : "";
      const text = kind === "gone" ? w.gone : kind === "undo-late" ? w.tooLate : kind === "changed" ? w.notUndone : w.wrong;
      setSaid({ ok: false, text, batch: null });
    } finally {
      flight.current = false;
      setBusy(false);
      await load();
    }
  }

  const keepOne = (line: string, id: string) =>
    press({ action: "keep", ids: [id] }, (m) => (m.kept === 0 ? { ok: false, text: w.nothingKept, batch: null } : { ok: true, text: w.kept(line), batch: m.batch }));
  const keepAll = (ids: string[]) =>
    press({ action: "keep", ids }, (m) => (m.kept === 0 ? { ok: false, text: w.nothingKept, batch: null } : { ok: true, text: w.keptMany(m.kept ?? ids.length), batch: m.batch }));
  const notRight = (line: string, id: string) =>
    press({ action: "not", id }, (m) => ({ ok: true, text: `${w.takenOut(line)} ${w.notAgain}`, batch: m.batch }));
  const undo = (batch: number) => press({ action: "undo", batch }, () => ({ ok: true, text: w.undone, batch: null }));
  // A feeling's step is changed in one tap; the line leaves the list and Undo takes it back (#4959).
  const changeFeeling = (f: WorldFeelingNote, step: WorldFeelingNote["step"]) =>
    press({ action: "bias", id: f.id, step }, (m) => ({ ok: true, text: w.kept(w.feelingLine(f.name, step, f.target)), batch: m.batch }));

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
  // The Roads group (#4784) is drawn only behind ROADS_LIVE; with the switch off, nothing about roads shows or is counted.
  const roads: WorldRoadNote[] = ROADS_LIVE ? (notes.roads ?? []) : [];
  // The Feelings group (#4959) is drawn only behind WORLD_FEELINGS_LIVE, and counted only then.
  const feelings: WorldFeelingNote[] = WORLD_FEELINGS_LIVE ? (notes.feelings ?? []) : [];
  const total = notes.places.length + notes.languages.length + roads.length + feelings.length;
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
                <button type="button" class="button secondary" disabled={busy} aria-label={w.notRightLabel(text(n))} onClick={() => void notRight(text(n), n.id)}>
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
      {roads.length > 0 && <RoadsGroup roads={roads} busy={busy} keepOne={keepOne} keepAll={keepAll} />}
      {feelings.length > 0 && (
        <FeelingsGroup
          feelings={feelings}
          busy={busy}
          keepOne={keepOne}
          keepAll={keepAll}
          notRight={notRight}
          changeFeeling={changeFeeling}
        />
      )}
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

/** The Roads group (#4784): one line per road, its name and the API's note, ✅ Keep (no 🚫 Not right) and Keep all for the shown lines.
 * A road's id is already its keep id, `road:<32 hex>`, as the door sends and reads it. */
function RoadsGroup({
  roads,
  busy,
  keepOne,
  keepAll,
}: {
  roads: WorldRoadNote[];
  busy: boolean;
  keepOne: (line: string, id: string) => Promise<void>;
  keepAll: (ids: string[]) => Promise<void>;
}) {
  const shown = roads.slice(0, SHOWN_AT_MOST);
  return (
    <div class="stack" key="road">
      <h3 class="small">{w.roads}</h3>
      <ul class="stack">
        {shown.map((n) => (
          <li key={n.id} class="stack">
            <p>{n.name}</p>
            <p>{n.note}</p>
            <p class="small muted">{n.fromSession !== null ? w.fromSession : w.fromLore}</p>
            <p class="row">
              <button type="button" class="button secondary" disabled={busy} aria-label={w.keepLabel(n.name)} onClick={() => void keepOne(n.name, n.id)}>
                {w.keep}
              </button>
            </p>
          </li>
        ))}
      </ul>
      {roads.length > shown.length && <p class="small muted">{w.showing(shown.length, roads.length)}</p>}
      <button type="button" class="button secondary" disabled={busy} onClick={() => void keepAll(shown.map((n) => n.id))}>
        {w.keepAllRoads(shown.length)}
      </button>
    </div>
  );
}

/** The Feelings group (#4959): one line per guess ("Mithral Keep is unfriendly toward elves") with ✅ Keep, ✏️ Change, 🚫 Not right, and Keep all
 * for the shown lines. Change opens in place with the five steps, the current one first, and Back. A feeling's keep id is `bias:<32 hex>`. */
function FeelingsGroup({
  feelings,
  busy,
  keepOne,
  keepAll,
  notRight,
  changeFeeling,
}: {
  feelings: WorldFeelingNote[];
  busy: boolean;
  keepOne: (line: string, id: string) => Promise<void>;
  keepAll: (ids: string[]) => Promise<void>;
  notRight: (line: string, id: string) => Promise<void>;
  changeFeeling: (f: WorldFeelingNote, step: WorldFeelingNote["step"]) => Promise<void>;
}) {
  const [changing, setChanging] = useState<string | null>(null);
  const shown = feelings.slice(0, SHOWN_AT_MOST);
  return (
    <div class="stack" key="feeling">
      <h3 class="small">{w.feelings}</h3>
      <ul class="stack">
        {shown.map((f) => {
          const line = w.feelingLine(f.name, f.step, f.target);
          const question = w.feelingQuestion(f.name, f.target);
          // The current step comes first, then the other four in order.
          const steps = [f.step, ...w.steps.filter((s) => s !== f.step)];
          return (
            <li key={f.id} class="stack">
              <p>{line}</p>
              <p class="small muted">{`${f.step} · ${w.fromLore}`}</p>
              <p class="row">
                <button type="button" class="button secondary" disabled={busy} aria-label={w.keepLabel(line)} onClick={() => void keepOne(line, f.id)}>
                  {w.keep}
                </button>
                <button
                  type="button"
                  class="button secondary"
                  disabled={busy}
                  aria-expanded={changing === f.id}
                  onClick={() => setChanging(changing === f.id ? null : f.id)}
                >
                  {w.changeFeeling}
                </button>
                <button type="button" class="button secondary" disabled={busy} aria-label={w.notRightLabel(line)} onClick={() => void notRight(line, f.id)}>
                  {w.notRight}
                </button>
              </p>
              {changing === f.id && (
                <div class="stack" role="group" aria-label={question}>
                  <p class="small">{question}</p>
                  <p class="row">
                    {steps.map((s) => (
                      <button
                        key={s}
                        type="button"
                        class={s === f.step ? "button" : "button secondary"}
                        disabled={busy}
                        onClick={() => {
                          setChanging(null);
                          void changeFeeling(f, s);
                        }}
                      >
                        {s}
                      </button>
                    ))}
                    <button type="button" class="button secondary" disabled={busy} onClick={() => setChanging(null)}>
                      {w.feelingBack}
                    </button>
                  </p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {feelings.length > shown.length && <p class="small muted">{w.showing(shown.length, feelings.length)}</p>}
      <button type="button" class="button secondary" disabled={busy} onClick={() => void keepAll(shown.map((f) => f.id))}>
        {w.keepAllFeelings(shown.length)}
      </button>
    </div>
  );
}
