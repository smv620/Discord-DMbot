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

  // The size and the kind are chosen first and saved with one Save (arrow keys move through a list without saving each step, as the
  // kind editor does). Each is its own queued change, so each has its own Undo; only what changed is sent.
  const sizeNow = size ?? "";
  const formNow = form ?? "land";
  const [pickSize, setPickSize] = useState(sizeNow);
  const [pickForm, setPickForm] = useState<string>(formNow);
  // The choice follows the row when a read changes what is saved (after a Save), part by part so one part's save never loses the other's
  // unsaved choice; the first paint does not reset it.
  const seen = useRef({ id: row.id, size: sizeNow, form: formNow });
  const latest = useRef({ size: sizeNow, form: formNow });
  latest.current = { size: sizeNow, form: formNow };
  if (seen.current.id !== row.id) {
    seen.current = { id: row.id, size: sizeNow, form: formNow };
    setPickSize(sizeNow);
    setPickForm(formNow);
    setNote(null);
  } else {
    if (seen.current.size !== sizeNow) {
      seen.current.size = sizeNow;
      setPickSize(sizeNow);
    }
    if (seen.current.form !== formNow) {
      seen.current.form = formNow;
      setPickForm(formNow);
    }
  }
  const changed = pickSize !== sizeNow || pickForm !== formNow;

  async function run(change: NameChange, ok: string): Promise<boolean> {
    const problem = await act(change, ok);
    if (problem === null) {
      setNote({ ok: true, text: ok });
      return true;
    }
    if (problem !== "") setNote({ ok: false, text: problem });
    return false;
  }

  async function save(): Promise<void> {
    if (flight.current || !changed) return;
    flight.current = true;
    setBusy(true);
    setNote(null);
    try {
      let fine = true;
      if (pickSize !== sizeNow) {
        fine = isSize(pickSize)
          ? await run({ action: "place-size", size: pickSize }, w.noted(row.name, pickSize))
          : await run({ action: "place-size", size: null }, w.notedNone(row.name));
        // The page's `act` also answers "nothing" for a change that may still be waiting or a name that has gone (it says so itself), so the
        // kind goes only once the list has been read again and shows the size saved.
        await new Promise((go) => setTimeout(go, 0));
        if (latest.current.size !== pickSize) fine = false;
      }
      if (fine && pickForm !== formNow) {
        const next: Form | null = (FORMS as readonly string[]).includes(pickForm) ? (pickForm as Form) : null;
        await run({ action: "place-form", form: next }, w.notedForm(row.name, next));
      }
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  async function setting(on: boolean): Promise<void> {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setNote(null);
    try {
      await run({ action: "place-setting", setting: on }, on ? w.notedSetting(row.name) : w.notedNoSetting);
    } finally {
      flight.current = false;
      setBusy(false);
    }
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
        {w.sizeLabel}
        <select id={`${id}-size`} value={pickSize} disabled={busy} aria-describedby={`${id}-why`} onChange={(e) => setPickSize((e.target as HTMLSelectElement).value)}>
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
        <select id={`${id}-form`} value={pickForm} disabled={busy} onChange={(e) => setPickForm((e.target as HTMLSelectElement).value)}>
          {(["land", ...FORMS] as const).map((f) => (
            <option key={f} value={f}>{`${w.forms[f]![0]}: ${w.forms[f]![1]}`}</option>
          ))}
        </select>
      </label>
      <button type="button" class="button secondary" disabled={busy || !changed} onClick={() => void save()}>
        {w.save}
      </button>
      {canSetting ? (
        <button
          type="button"
          class="button secondary"
          disabled={busy || changed}
          onClick={() => void setting(row.setting !== true)}
        >
          {row.setting === true ? w.settingRemove : w.settingMake}
        </button>
      ) : (
        <p class="small muted">{w.tooSmall}</p>
      )}
      <p role="status" aria-live="polite" class={note && !note.ok ? "warn small" : "small"}>
        {busy ? w.working : (note?.text ?? "")}
      </p>
    </div>
  );
}
