/** @jsxImportSource preact */
// A place's size, kind and setting on a name of the Names page (#4187, the website half of #4173): the size line, "📏 Size" with a
// choice and an example under each, "Land, water or a road?", and "🌍 Make it the setting". The same words as Discord's place card.
// A separate file, fetched only when a DM opens a place to change it. Every press goes through the page's own `act` (so the list is read
// again, an Undo chance ends and a signed-out DM is sent to sign in). Anyone who can see a place sees its size and kind: they are not secrets,
// but only the DM has these controls. Shown by the Names page only for a place, to a DM, while PLACE_FACTS_LIVE.
import { useRef, useState } from "preact/hooks";

import { FORMS, placeWords as w, SIZES, type Form, type Size } from "../content/place-words";
import type { NameChange, NameRow } from "./api";

const isSize = (v: string | null | undefined): v is Size => (SIZES as readonly string[]).includes(v ?? "");
const BIG: readonly string[] = ["plane", "world", "region"]; // a setting is a region or bigger

export default function PlaceFacts({ row, act }: { row: NameRow; act: (change: NameChange, ok: string) => Promise<string | null> }) {
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const size = isSize(row.placeLevel) ? row.placeLevel : null;
  const form: Form | null = row.form === "water" || row.form === "route" ? row.form : null;
  const id = `place-${row.id}`;

  async function run(change: NameChange, ok: string): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setNote(null);
    const problem = await act(change, ok);
    flight.current = false;
    setBusy(false);
    if (problem === null) setNote({ ok: true, text: ok });
    else if (problem !== "") setNote({ ok: false, text: problem });
  }

  const canSetting = row.setting === true || (size !== null && BIG.includes(size));
  return (
    <div class="name-form stack" aria-busy={busy}>
      {size && (
        <p>
          {w.sizeLine(size)}
          {row.placeLevelFrom === "read" ? ` ${w.readNote}` : ""}
        </p>
      )}
      {form && (
        <p>
          {w.formLine(form)}
          {row.formFrom === "read" ? ` ${w.readNote}` : ""}
        </p>
      )}
      {row.setting === true && <p>{w.settingLine}</p>}
      <label class="field" for={`${id}-size`}>
        {w.sizeAsk}
        <select
          id={`${id}-size`}
          value={size ?? ""}
          disabled={busy}
          aria-describedby={`${id}-why`}
          onChange={(e) => {
            const picked = (e.target as HTMLSelectElement).value;
            if (picked === "") return void run({ action: "place-size", size: null }, w.notedNone(row.name));
            if (isSize(picked)) void run({ action: "place-size", size: picked }, w.noted(row.name, picked));
          }}
        >
          <option value="">{`${w.notSure[0]}: ${w.notSure[1]}`}</option>
          {SIZES.map((s) => (
            <option key={s} value={s}>{`${w.sizes[s][0]}: ${w.sizes[s][1]}`}</option>
          ))}
        </select>
      </label>
      <span id={`${id}-why`} class="small muted">
        {w.why}
      </span>
      <label class="field" for={`${id}-form`}>
        {w.formAsk}
        <select
          id={`${id}-form`}
          value={form ?? "land"}
          disabled={busy}
          onChange={(e) => {
            const picked = (e.target as HTMLSelectElement).value;
            const next: Form | null = (FORMS as readonly string[]).includes(picked) ? (picked as Form) : null;
            void run({ action: "place-form", form: next }, w.notedForm(row.name, next));
          }}
        >
          {(["land", ...FORMS] as const).map((f) => (
            <option key={f} value={f}>{`${w.forms[f]![0]}: ${w.forms[f]![1]}`}</option>
          ))}
        </select>
      </label>
      {canSetting ? (
        <button
          type="button"
          class="button secondary"
          disabled={busy}
          onClick={() => void run({ action: "place-setting", setting: row.setting !== true }, row.setting === true ? w.notedNoSetting : w.notedSetting(row.name))}
        >
          {row.setting === true ? w.settingRemove : w.settingMake}
        </button>
      ) : (
        <p class="small muted">{w.tooSmall}</p>
      )}
      <p role="status" aria-live="polite" class={note && !note.ok ? "warn small" : "small"}>
        {note?.text ?? ""}
      </p>
    </div>
  );
}
