/** @jsxImportSource preact */
// The "Type in my character" panel (#2832): a player types in a character's class, level, species and names when no
// sheet is linked. It only draws the fields and reports what is typed; the page checks it (checkTyped) and sends it.
import { sheetWords as w } from "../content/sheets-words";
import type { SheetCharacter } from "./api";
import type { TypedValues } from "./sheets-typed";

export default function TypedForm({
  sheet,
  values,
  busy,
  onValues,
  onSubmit,
}: {
  sheet: SheetCharacter;
  values: TypedValues;
  busy: boolean;
  onValues: (patch: Partial<TypedValues>) => void;
  onSubmit: () => void;
}) {
  return (
    <details>
      <summary>{w.typedSummary}</summary>
      <form
        class="stack"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        {/* No native "required": the browser's own English message would replace typedNeedsClass. */}
        <fieldset class="stack">
          <legend class="visually-hidden">{w.typedFor(sheet.name)}</legend>
          <label>
            {w.typedClass}
            <input
              type="text"
              maxLength={60}
              value={values.className}
              placeholder={w.typedClassHint}
              onInput={(e) => onValues({ className: (e.target as HTMLInputElement).value })}
            />
          </label>
          <label>
            {w.typedLevel}
            <input
              type="text"
              inputMode="numeric"
              maxLength={2}
              value={values.level}
              placeholder={w.typedLevelHint}
              onInput={(e) => onValues({ level: (e.target as HTMLInputElement).value })}
            />
          </label>
          <label>
            {w.typedSpecies}
            <input
              type="text"
              maxLength={60}
              value={values.species}
              placeholder={w.typedSpeciesHint}
              onInput={(e) => onValues({ species: (e.target as HTMLInputElement).value })}
            />
          </label>
          <label>
            {w.typedNames}
            <textarea
              maxLength={1500}
              value={values.names}
              placeholder={w.typedNamesHint}
              onInput={(e) => onValues({ names: (e.target as HTMLTextAreaElement).value })}
            />
          </label>
        </fieldset>
        {sheet.source === "dndbeyond" && <p class="small muted">{w.typedReplacesLink}</p>}
        <button type="submit" class="button" aria-disabled={busy}>
          {w.typedSave}
        </button>
      </form>
    </details>
  );
}
