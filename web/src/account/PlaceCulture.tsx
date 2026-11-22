/** @jsxImportSource preact */
// A place's culture and who lives here on the Names page (#4412, the DM's half): the two lines, in Discord's words, and the
// pickers that set them. Each is saved on its own: a lineage's row is replaced and the others stay, "Not sure" clears every row
// of the layer. Shown inside the place's panel (PlaceWorld.tsx), so it is behind PLACE_WORLD_LIVE like the other layers.
import { useState } from "preact/hooks";

import { placeWorldWords as w } from "../content/place-world-words";
import type { NameChange, PlaceWorld, PlaceWorldRow } from "./api";

import { LINEAGES, cultureLine, populationLine } from "../content/place-culture-lines";

export default function PlaceCulture({
  data,
  name,
  busy,
  send,
}: {
  data: PlaceWorld;
  name: string;
  busy: boolean;
  send: (change: NameChange, ok: string, matches: (got: PlaceWorld) => boolean) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const cultures = data.rows.filter((r) => r.layer === "culture");
  const people = data.rows.filter((r) => r.layer === "population");
  const cultureLines = cultureLine(cultures);
  const peopleLines = populationLine(people);
  const named = data.cultures ?? [];

  // The pickers start from what is saved, so opening one never clears it; a named culture is chosen by its id.
  const savedC = cultures[0];
  const savedP = people[0];
  const valueOf = (r: PlaceWorldRow | undefined): string => (r ? (r.cultureId ?? r.value) : "");
  const wordsOf = (r: PlaceWorldRow | undefined): string => (r && r.value === "other" && !r.cultureId ? r.words : (r?.culture ?? ""));
  const [cValue, setCValue] = useState(valueOf(savedC));
  const [cHow, setCHow] = useState(savedC?.how ?? "");
  const [cWords, setCWords] = useState(wordsOf(savedC));
  const [pValue, setPValue] = useState(valueOf(savedP));
  const [pHow, setPHow] = useState(savedP?.how ?? "");
  const [pWords, setPWords] = useState(wordsOf(savedP));

  const cultureReady = cValue === "" || (cHow !== "" && (cValue !== "other" || cWords.trim() !== ""));
  const peopleReady = pValue === "" || (pValue !== "other" || pWords.trim() !== "");
  const cultureChanged = cValue !== valueOf(savedC) || (cValue !== "" && (cHow !== (savedC?.how ?? "") || (cValue === "other" && cWords.trim() !== wordsOf(savedC))));
  const peopleChanged = pValue !== valueOf(savedP) || (pValue !== "" && (pHow !== (savedP?.how ?? "") || (pValue === "other" && pWords.trim() !== wordsOf(savedP))));

  // The read-back check: the saved rows show this choice (a named culture by its name, the other lineages untouched).
  const cultureSaved = (g: PlaceWorld): boolean => {
    const rows = g.rows.filter((r) => r.layer === "culture");
    if (cValue === "") return rows.length === 0;
    return rows.some((r) => (cValue === "other" ? r.culture === cWords.trim() : (r.cultureId ?? r.value) === cValue) && r.how === cHow);
  };
  const peopleSaved = (g: PlaceWorld): boolean => {
    const rows = g.rows.filter((r) => r.layer === "population");
    if (pValue === "") return rows.length === 0;
    return rows.some((r) => (pValue === "other" ? r.culture === pWords.trim() : r.value === pValue) && r.how === pHow);
  };

  async function saveCulture(): Promise<void> {
    if (busy) return;
    const change: NameChange =
      cValue === ""
        ? { action: "map-culture", value: null }
        : { action: "map-culture", value: cValue, how: cHow, ...(cValue === "other" ? { words: cWords.trim() } : {}) };
    await send(change, w.notedCulture(name), cultureSaved);
  }

  async function savePeople(): Promise<void> {
    if (busy) return;
    const change: NameChange =
      pValue === ""
        ? { action: "map-population", value: null }
        : { action: "map-population", value: pValue, how: pHow, ...(pValue === "other" ? { words: pWords.trim() } : {}) };
    await send(change, w.notedPopulation(name), peopleSaved);
  }

  return (
    <div class="stack">
      {cultureLines && <p>{cultureLines}</p>}
      {peopleLines && <p>{peopleLines}</p>}
      <button type="button" class="button secondary" aria-expanded={open} disabled={busy} onClick={() => setOpen(!open)}>
        {w.culture.heading}
      </button>
      {open && (
        <div class="stack" role="group" aria-label={w.culture.heading}>
          <label class="field">
            {w.culture.whose}
            <select value={cValue} disabled={busy} onChange={(e) => setCValue((e.target as HTMLSelectElement).value)}>
              <option value="">{w.culture.notSure}</option>
              {named.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
              {LINEAGES.map((l) => (
                <option key={l} value={l}>{w.culture.adjective[l]}</option>
              ))}
              <option value="other">{w.culture.something}</option>
            </select>
          </label>
          {cValue === "other" && (
            <input type="text" value={cWords} maxLength={60} placeholder={w.culture.wordsLabel} disabled={busy} onInput={(e) => setCWords((e.target as HTMLInputElement).value)} />
          )}
          {cValue !== "" && (
            <label class="field">
              {w.culture.howStrong}
              <select value={cHow} disabled={busy} onChange={(e) => setCHow((e.target as HTMLSelectElement).value)}>
                <option value="">—</option>
                {w.culture.strengthChoices.map(([v, label]) => (
                  <option key={v} value={v}>{label}</option>
                ))}
              </select>
            </label>
          )}
          <button type="button" class="button" disabled={busy || !cultureReady || !cultureChanged} onClick={() => void saveCulture()}>
            {w.save}
          </button>
          <label class="field">
            {w.population.whoLives}
            <select value={pValue} disabled={busy} onChange={(e) => setPValue((e.target as HTMLSelectElement).value)}>
              <option value="">{w.culture.notSure}</option>
              {LINEAGES.map((l) => (
                <option key={l} value={l}>{w.culture.plural[l]}</option>
              ))}
              <option value="other">{w.culture.something}</option>
            </select>
          </label>
          {pValue === "other" && (
            <input type="text" value={pWords} maxLength={60} placeholder={w.culture.wordsLabel} disabled={busy} onInput={(e) => setPWords((e.target as HTMLInputElement).value)} />
          )}
          {pValue !== "" && (
            <label class="field">
              {w.population.howMany}
              <select value={pHow} disabled={busy} onChange={(e) => setPHow((e.target as HTMLSelectElement).value)}>
                <option value="">{w.population.notSaid}</option>
                {w.population.shareChoices.map(([v, label]) => (
                  <option key={v} value={v}>{label}</option>
                ))}
              </select>
            </label>
          )}
          <button type="button" class="button" disabled={busy || !peopleReady || !peopleChanged} onClick={() => void savePeople()}>
            {w.save}
          </button>
        </div>
      )}
    </div>
  );
}
