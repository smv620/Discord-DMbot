/** @jsxImportSource preact */
// "Fix a name everywhere" inside the recap editor (#2630): the DM types a spelling that is wrong and the
// right one, and every use in the text box is replaced at once. It changes the text in the box only;
// nothing is saved until the DM presses Save, so it needs no route of its own. The names list is not
// touched here (that is the name pass, #2628). Matching is whole words and ignores capitals.
import { useState } from "preact/hooks";

import { fixNameWords as words } from "../content/recap-panel";

const escape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Replaces every whole-word use of `wrong` in `text`; returns the new text and how many were replaced. */
export function replaceName(text: string, wrong: string, right: string): { text: string; times: number } {
  const needle = wrong.trim();
  if (needle === "" || needle === right.trim()) return { text, times: 0 };
  // Cues (《mood》 and ((pause))) are not the story: a name inside one is left alone, or the music's mood
  // would change and the whole audio be made again for a spelling fix.
  if (/(《[^》]*》|\(\([^)]*\)\))/.test(text)) {
    let times = 0;
    const parts = text.split(/(《[^》]*》|\(\([^)]*\)\))/);
    const out = parts.map((part, i) => {
      if (i % 2 === 1) return part; // (the odd parts are the cues the split kept)
      const found = replaceName(part, wrong, right);
      times += found.times;
      return found.text;
    });
    return { text: out.join(""), times };
  }
  // \b fails next to an apostrophe or a non-letter edge, so the edges are checked by hand.
  const pattern = new RegExp(`(^|[^\\p{L}\\p{N}_])(${escape(needle)})(?![\\p{L}\\p{N}_])`, "giu");
  let times = 0;
  const next = text.replace(pattern, (_all, before: string) => {
    times += 1;
    return `${before}${right.trim()}`;
  });
  return { text: next, times };
}

export default function RecapFixName({ text, onText }: { text: string; onText: (next: string) => void }) {
  const [wrong, setWrong] = useState("");
  const [right, setRight] = useState("");
  const [said, setSaid] = useState<string | null>(null);

  const run = (): void => {
    const found = replaceName(text, wrong, right);
    if (found.times === 0) {
      setSaid(words.none(wrong.trim()));
      return;
    }
    onText(found.text);
    setSaid(words.done(wrong.trim(), right.trim(), found.times));
  };

  return (
    <fieldset class="panel">
      <legend>{words.heading}</legend>
      <label for="fix-name-wrong">{words.wrongLabel}</label>
      <input id="fix-name-wrong" type="text" value={wrong} onInput={(e) => {
          setWrong((e.target as HTMLInputElement).value);
          setSaid(null);
        }} />
      <label for="fix-name-right">{words.rightLabel}</label>
      <input id="fix-name-right" type="text" value={right} onInput={(e) => {
          setRight((e.target as HTMLInputElement).value);
          setSaid(null);
        }} />
      <p class="muted small">{words.note}</p>
      <button type="button" class="button secondary" disabled={wrong.trim() === "" || right.trim() === ""} onClick={run}>
        {words.button}
      </button>
      <p role="status" class="small">
        {said}
      </p>
    </fieldset>
  );
}
