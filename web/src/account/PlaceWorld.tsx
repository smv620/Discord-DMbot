/** @jsxImportSource preact */
// A place's land, climate and ruler on a name of the Names page (#4287, MapBot layers slice 2, the DM's half): the card's lines and the
// choices, in Discord's words. A separate file, fetched only when a DM opens a place to change it. The rows come from a DM-only door (it
// answers nothing to a player). Land, climate and ruler are chosen, then saved with one Save: each is its own queued change with its
// own Undo, only what changed is sent, and a later one is sent only once the page has read the earlier one back as saved. Every press goes
// through the page's own `act`. What players see of these is another piece (#4311). Shown by the Names page for a place, to a DM, while
// PLACE_WORLD_LIVE.
import { useEffect, useRef, useState } from "preact/hooks";

import { HOWS, LAND, placeWorldWords as w, WARMTH } from "../content/place-world-words";
import type { AccountApi, NameChange, NameRow, PlaceWorld, PlaceWorldRow } from "./api";

const BIG: readonly string[] = ["plane", "world", "region"]; // a climate is for a region or bigger

interface Picks {
  land: string;
  part: boolean;
  warm: string;
  wet: string;
  group: string;
  how: string;
}

// (a row with a part, "the northern half", is not what the store's own setter changes: only the whole-place row counts here)
const whole = (data: PlaceWorld, layer: string): PlaceWorldRow[] => data.rows.filter((r) => r.layer === layer && r.part === "");

type Scope = "all" | "land" | "climate" | "ruler" | "none";

function picksOf(data: PlaceWorld): Picks {
  const by = (layer: string): PlaceWorldRow | undefined => whole(data, layer)[0];
  const land = by("terrain");
  const climate = by("climate");
  const control = by("control");
  return { land: land?.value ?? "", part: land?.how === "partly", warm: climate?.value ?? "", wet: climate?.how ?? "", group: control?.group ?? "", how: control?.how ?? "" };
}

export default function PlaceWorldPanel({ api, refId, row, act }: { api: AccountApi; refId: string; row: NameRow; act: (change: NameChange, ok: string) => Promise<string | null> }) {
  const [data, setData] = useState<PlaceWorld | null>(null);
  const [failed, setFailed] = useState(false);
  const [picks, setPicks] = useState<Picks | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);

  /** Read the place again. `scope` is the part of the choices to reset to what is saved: all of them for the first read, only the layer just
   * saved after a Save (so a choice not sent yet is never thrown away), none after the show switch. */
  async function load(scope: Scope = "all"): Promise<PlaceWorld | null> {
    if (!api.placeWorld) {
      setFailed(true);
      return null;
    }
    try {
      const got = await api.placeWorld(refId, row.id);
      setData(got);
      setFailed(false);
      const saved = picksOf(got);
      setPicks((now) => {
        if (scope === "all" || now === null) return saved;
        if (scope === "land") return { ...now, land: saved.land, part: saved.part };
        if (scope === "climate") return { ...now, warm: saved.warm, wet: saved.wet };
        if (scope === "ruler") return { ...now, group: saved.group, how: saved.how };
        return now;
      });
      return got;
    } catch {
      setFailed(true);
      return null;
    }
  }
  useEffect(() => {
    setPicks(null);
    setNote(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refId, row.id]);

  if (failed)
    return (
      <div role="alert">
        <p>{w.failed}</p>
        <button type="button" class="button secondary" onClick={() => void load()}>
          {w.tryAgain}
        </button>
      </div>
    );
  if (!data || !picks)
    return (
      <p class="muted" role="status">
        {w.loading}
      </p>
    );

  const saved = picksOf(data);
  const by = (layer: string): PlaceWorldRow | undefined => whole(data, layer)[0];
  const land = by("terrain");
  const climate = by("climate");
  const controls = whole(data, "control"); // (a place may be ruled by one group and claimed by another)
  // A value the story put in its own words ("other") can be shown but not set from here: Discord's menus do not offer it either.
  const landOwn = land?.value === "other";
  const climateOwn = climate?.value === "other";
  const canClimate = data.level === null || BIG.includes(data.level);
  const isLand = data.form === null; // terrain is for land, not water or a road
  const changedLand = !landOwn && (picks.land !== saved.land || (picks.land !== "" && picks.part !== saved.part));
  const changedClimate = !climateOwn && (picks.warm !== saved.warm || (picks.warm !== "" && picks.wet !== saved.wet));
  const changedRuler = picks.group !== saved.group || (picks.group !== "" && picks.how !== saved.how);
  const changed = changedLand || changedClimate || changedRuler;
  const shown = data.rows.some((r) => r.shown);
  const id = `world-${row.id}`;
  const set = (patch: Partial<Picks>) => setPicks({ ...picks, ...patch });

  async function sendLayer(scope: Scope, change: NameChange, ok: string, matches: (got: PlaceWorld) => boolean): Promise<boolean> {
    const problem = await act(change, ok);
    if (problem !== null) {
      if (problem !== "") setNote({ ok: false, text: problem });
      return false;
    }
    // The page's `act` answers "nothing" for a change that may still be waiting, so the next layer goes only once the saved rows show this one.
    await new Promise((go) => setTimeout(go, 0));
    const got = await load(scope);
    if (got === null || !matches(got)) {
      setNote({ ok: false, text: w.stillSaving });
      return false;
    }
    setNote({ ok: true, text: ok });
    return true;
  }

  async function save(): Promise<void> {
    if (flight.current || !changed) return;
    flight.current = true;
    setBusy(true);
    setNote(null);
    try {
      let fine = true;
      if (changedLand) {
        const value = picks!.land === "" ? null : picks!.land;
        fine = await sendLayer("land", { action: "map-terrain", value, how: value && picks!.part ? "partly" : "" }, w.notedLand(row.name), (g) => picksOf(g).land === (value ?? ""));
      }
      if (fine && changedClimate) {
        const value = picks!.warm === "" ? null : picks!.warm;
        fine = await sendLayer("climate", { action: "map-climate", value, how: value ? picks!.wet : "" }, w.notedClimate(row.name), (g) => picksOf(g).warm === (value ?? ""));
      }
      if (fine && changedRuler) {
        const group = picks!.group === "" ? null : picks!.group;
        // A ruler needs a way of holding the place; "rules it" is the first and the plainest.
        const how = group ? picks!.how || "rules" : "";
        await sendLayer("ruler", { action: "map-control", group, how }, w.notedRuler(row.name), (g) => picksOf(g).group === (group ?? ""));
      }
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  async function toggleShown(): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setNote(null);
    try {
      await sendLayer("none", { action: "map-show", shown: !shown }, w.notedShown(row.name, !shown), (g) => g.rows.some((r) => r.shown) === !shown);
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  const readMark = (r: PlaceWorldRow | undefined): string => (r?.read ? w.readMark : "");
  const landWord = land ? (land.value === "other" ? land.words : (w.land.choices[land.value]?.[0] ?? land.value).toLowerCase()) : "";
  const warmWord = climate ? (w.climate.warmth[climate.value] ?? climate.value).toLowerCase() : "";
  return (
    <div class="name-form stack" aria-busy={busy}>
      <h4>{w.heading}</h4>
      {land && (
        <p>
          {w.landLine(landWord, land.how === "partly")}
          {readMark(land)}
        </p>
      )}
      {climate && (
        <p>
          {w.climateLine(warmWord, climate.how)}
          {readMark(climate)}
        </p>
      )}
      {controls.map((c) => (
        <p key={`${c.group}-${c.how}`}>
          {c.groupName ? w.rulerLine(w.ruler.lines[c.how] ?? w.ruler.lines["rules"]!, c.groupName) : ""}
          {readMark(c)}
        </p>
      ))}

      {isLand && !landOwn && (
        <>
          <label class="field" for={`${id}-land`}>
            {w.land.label}
            <select id={`${id}-land`} value={picks.land} disabled={busy} onChange={(e) => set({ land: (e.target as HTMLSelectElement).value })}>
              <option value="">{w.land.notSure}</option>
              {LAND.map((v) => (
                <option key={v} value={v}>{`${w.land.choices[v]![0]}: ${w.land.choices[v]![1]}`}</option>
              ))}
            </select>
          </label>
          {picks.land !== "" && (
            <label class="field" for={`${id}-part`}>
              <input id={`${id}-part`} type="checkbox" checked={picks.part} disabled={busy} onChange={(e) => set({ part: (e.target as HTMLInputElement).checked })} /> {w.land.part}
            </label>
          )}
        </>
      )}

      {climateOwn ? null : canClimate ? (
        <>
          <label class="field" for={`${id}-climate`}>
            {w.climate.label}
            <select id={`${id}-climate`} value={picks.warm} disabled={busy} onChange={(e) => set({ warm: (e.target as HTMLSelectElement).value })}>
              <option value="">{w.climate.notSure}</option>
              {WARMTH.map((v) => (
                <option key={v} value={v}>{w.climate.warmth[v]}</option>
              ))}
            </select>
          </label>
          {picks.warm !== "" && (
            <label class="field" for={`${id}-wet`}>
              {w.climate.wetLabel}
              <select id={`${id}-wet`} value={picks.wet} disabled={busy} onChange={(e) => set({ wet: (e.target as HTMLSelectElement).value })}>
                <option value="">{w.climate.neither}</option>
                <option value="dry">{w.climate.dry}</option>
                <option value="wet">{w.climate.wet}</option>
              </select>
            </label>
          )}
        </>
      ) : (
        <p class="small muted">{w.climate.tooSmall}</p>
      )}

      <label class="field" for={`${id}-group`}>
        {w.ruler.label}
        <select id={`${id}-group`} value={picks.group} disabled={busy} onChange={(e) => set({ group: (e.target as HTMLSelectElement).value })}>
          <option value="">{w.ruler.notSure}</option>
          {data.groups.map((g) => (
            <option key={g.id} value={g.id}>{g.name}</option>
          ))}
        </select>
      </label>
      {data.groups.length === 0 && <p class="small muted">{w.ruler.noGroups}</p>}
      {picks.group !== "" && (
        <div role="radiogroup" aria-label={w.ruler.howLabel}>
          <span class="small">{w.ruler.howLabel}</span>
          {HOWS.map((h) => (
            <label key={h} class="row">
              <input type="radio" name={`${id}-how`} checked={(picks.how || "rules") === h} disabled={busy} onChange={() => set({ how: h })} /> {w.ruler.hows[h]}
            </label>
          ))}
        </div>
      )}

      <button type="button" class="button secondary" disabled={busy || !changed} onClick={() => void save()}>
        {w.save}
      </button>
      {data.rows.length > 0 ? (
        <button type="button" class="button secondary" disabled={busy || changed} onClick={() => void toggleShown()}>
          {shown ? w.hide : w.show}
        </button>
      ) : (
        <p class="small muted">{w.nothingToShow}</p>
      )}
      {data.rows.length > 0 && changed && <p class="small muted">{w.saveFirst}</p>}
      <p role="status" aria-live="polite" class={note && !note.ok ? "warn small" : "small"}>
        {busy ? w.working : (note?.text ?? "")}
      </p>
    </div>
  );
}
