/** @jsxImportSource preact */
// The recap must-haves on the Recap style page (#3329; docs/RECAP_STANDARD.md): the list of what every recap of the
// campaign covers when the session had it. DMbot's own thirteen are shown under their five headings; the DM edits a
// line in place, removes one, adds one and saves the whole list in one go (so a half-edited list is never sent), or
// puts DMbot's own back (asked once). The page above follows the save to its end with the shared wait panel; this part
// only holds the draft. Words and limits are in recap-style-words.ts.
import { useEffect, useRef, useState } from "preact/hooks";

import { MUSTS_LINE_CHARS, MUSTS_MAX_LINES, mustsPage as words } from "../content/recap-style-words";
import type { RecapMusts } from "./api";

interface Draft {
  key: number;
  group: string;
  text: string;
}

const same = (a: string[], b: string[]): boolean => a.length === b.length && a.every((t, i) => t === b[i]);

export default function RecapMustsPanel({
  musts,
  disabled,
  onSave,
  onDefaults,
}: {
  musts: RecapMusts;
  /** A change is being sent or followed: the controls rest until it ends, as the examples' do. */
  disabled: boolean;
  onSave: (lines: string[]) => void;
  /** Asked once here; the page sends it. */
  onDefaults: () => void;
}) {
  const keys = useRef(0);
  const fresh = (list: RecapMusts): Draft[] => list.lines.map((l) => ({ key: keys.current++, group: l.group, text: l.text }));
  const [draft, setDraft] = useState<Draft[]>(() => fresh(musts));
  const [adding, setAdding] = useState("");
  const [asking, setAsking] = useState(false);
  // What the API shows is the truth: when it changes (a save finished, the defaults came back) the draft starts over from it.
  // The draft starts over only when what the API shows really differs from what it showed: a reload that brings the
  // same list (after an example or a note was added on this page) must not wipe edits that are not saved yet.
  // (Ids are positions, not stable across saves, so only the words and whether it is DMbot's own list are compared.)
  const signature = (list: RecapMusts): string => JSON.stringify([list.defaults, list.lines.map((l) => l.text)]);
  const shown = useRef(signature(musts));
  useEffect(() => {
    const now = signature(musts);
    if (shown.current === now) return; // (also the first look: the draft already starts from it)
    shown.current = now;
    setDraft(fresh(musts));
    setAdding("");
    setAsking(false);
  }, [musts]);

  // A line typed in "Add a line" but not added yet goes with Save, so it is never lost.
  const texts = [...draft.map((d) => d.text.trim()), adding.trim()].filter((t) => t !== "");
  const changed = !same(texts, musts.lines.map((l) => l.text));
  const full = draft.length >= MUSTS_MAX_LINES;
  const tooLong = draft.some((d) => d.text.length > MUSTS_LINE_CHARS);

  // Headings where the group changes; a line with no group sits under "Added by you".
  const rows: { heading: string | null; d: Draft; n: number }[] = [];
  let last: string | null = null;
  draft.forEach((d, i) => {
    const heading = d.group === "" ? words.ownHeading : (words.groups[d.group] ?? d.group);
    rows.push({ heading: heading !== last ? heading : null, d, n: i + 1 });
    last = heading;
  });
  // Show the headings only while there are groups to tell apart (a list of one's own has just "Added by you").
  const grouped = draft.some((d) => d.group !== "");

  return (
    <section class="stack" aria-labelledby="rs-musts">
      <h2 id="rs-musts">{words.heading}</h2>
      <p>{words.intro}</p>
      <p class="small">{musts.defaults ? words.usingDefaults : words.usingOwn(musts.lines.length)}</p>
      <ol class="stack">
        {rows.map(({ heading, d, n }) => (
          <li key={d.key} class="stack">
            {grouped && heading && <h3 class="small">{heading}</h3>}
            <div class="row">
              <input
                type="text"
                class="wide"
                maxLength={MUSTS_LINE_CHARS}
                value={d.text}
                disabled={disabled}
                aria-label={words.lineLabel(n)}
                onInput={(e) => {
                  const text = (e.target as HTMLInputElement).value;
                  setDraft((now) => now.map((x) => (x.key === d.key ? { ...x, text } : x)));
                }}
              />
              <button
                type="button"
                class="button secondary"
                disabled={disabled}
                aria-label={`${words.remove(n)}: ${d.text.trim().slice(0, 30) || words.emptyLine}`}
                onClick={() => setDraft((now) => now.filter((x) => x.key !== d.key))}
              >
                {words.remove(n)}
              </button>
            </div>
          </li>
        ))}
      </ol>
      {full ? (
        <p>{words.full}</p>
      ) : (
        <form
          class="stack"
          onSubmit={(e) => {
            e.preventDefault();
            const text = adding.trim();
            if (text === "") return;
            setDraft((now) => [...now, { key: keys.current++, group: "", text }]);
            setAdding("");
          }}
        >
          <label for="rs-must-add">{words.addLabel}</label>
          <input id="rs-must-add" type="text" maxLength={MUSTS_LINE_CHARS} value={adding} disabled={disabled} aria-describedby="rs-must-add-help" onInput={(e) => setAdding((e.target as HTMLInputElement).value)} />
          <p id="rs-must-add-help" class="small">
            {words.addHelp}
          </p>
          <p>
            <button type="submit" class="button secondary" disabled={disabled || adding.trim() === ""}>
              {words.addButton}
            </button>
          </p>
        </form>
      )}
      <p role="status" class="small">
        {texts.length === 0 ? words.emptyList : changed ? words.unsaved : ""}
      </p>
      <p>
        <button
          type="button"
          class="button"
          disabled={disabled || !changed || tooLong || texts.length === 0}
          onClick={() => {
            setAdding("");
            onSave(texts);
          }}
        >
          {words.save}
        </button>{" "}
        {!musts.defaults && (
          <button type="button" class="button secondary" disabled={disabled} onClick={() => setAsking(true)}>
            {words.defaultsButton}
          </button>
        )}
      </p>
      {asking && (
        <div class="stack" role="group" aria-label={words.defaultsAsk}>
          <p role="alert">{words.defaultsAsk}</p>
          <p>
            <button type="button" class="button" disabled={disabled} onClick={() => { setAsking(false); onDefaults(); }}>
              {words.defaultsYes}
            </button>{" "}
            <button type="button" class="button secondary" onClick={() => setAsking(false)}>
              {words.keep}
            </button>
          </p>
        </div>
      )}
    </section>
  );
}
