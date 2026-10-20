/** @jsxImportSource preact */
// The two cue parts of the recap page (#2084): the words a cue may use, and the problems a refused
// save or approval found. Shared by the edit form and the approval part. The API says what is
// wrong and which words are allowed; this only shows it.
import { forwardRef } from "preact/compat";

import {
  cueProblemLine,
  cueProblemsHeading,
  cueProblemsMore,
  cueWords,
} from "../content/recaps";
import type { CueProblem, CueWords } from "./api";

/** The words a cue may use, in a small panel the DM can open (#2084). The API says them, so the
 * page shows exactly what it checks. Open by itself after a refused save with an unknown word. */
export function CueWordsHelp({
  words,
  open,
}: {
  words: CueWords;
  open: boolean;
}) {
  const synonyms = Object.entries(words.synonyms).map(
    ([from, to]) => `${from} works as ${to}`,
  );
  return (
    <details class="cue-help" open={open || undefined}>
      <summary>{cueWords.help}</summary>
      <p class="muted small">{cueWords.helpLead}</p>
      <p>
        <strong>{cueWords.moods}:</strong> {words.moods.join(", ")}
      </p>
      {synonyms.length > 0 && (
        <p>
          <strong>{cueWords.synonyms}:</strong> {synonyms.join(", ")}
        </p>
      )}
      <p>
        <strong>{cueWords.pacing}:</strong> {words.pacing.join(", ")}
      </p>
    </details>
  );
}

/** The cue problems a refused save found, under the text box (#2084): the first few, in the
 * order the API gave, and focus comes here so a screen reader and a phone land on them. */
export const CueProblems = forwardRef<
  HTMLDivElement,
  { problems: CueProblem[]; help: boolean; action?: "save" | "approve" }
>(function CueProblems({ problems, help, action = "save" }, ref) {
  const shown = problems.slice(0, cueWords.maxShown);
  return (
    <div id="recap-cue-problems" class="cue-problems" tabIndex={-1} ref={ref}>
      <p>
        <strong>{cueProblemsHeading(problems.length, action)}</strong>
      </p>
      <ul>
        {shown.map((problem, i) => (
          <li key={i}>{cueProblemLine(problem, help)}</li>
        ))}
      </ul>
      {problems.length > shown.length && (
        <p>{cueProblemsMore(problems.length - shown.length)}</p>
      )}
      <p class="muted small">{cueWords.paragraphs}</p>
    </div>
  );
});
