// The pretend AI feed of the preview builds (#3710): a few calls that arrive one by one as the pretend job runs. Kept
// out of mock.ts and loaded on demand, because every page that carries the pretend API counts toward the page weight.
import type { AiFeedRead } from "./api";

const ASKED = [
  "Read this passage of the lore and list the names in it.\n\nThe ferry crossed the Brynwater at dusk. Old Tam took the coin.",
  "Read the next passage and list the names and how they connect.\n\nMira Voss, the smith, owed Garrick a debt from the winter.",
  "Which of these names are the same person?\n\nThe Ferry Lady; Old Tam's wife; Mira",
];
const BACK = ["Names: Brynwater (place), Old Tam (person).", "Names: Mira Voss (person), Garrick (person). Mira owes Garrick.", "The Ferry Lady may be Old Tam's wife."];

/** The feed as it stands at the `looks`-th look: one more call each look, each answered a look later. */
export function pretendFeed(job: string, looks: number): AiFeedRead {
  const base = 1_790_000_000;
  const shown = Math.min(ASKED.length, looks);
  return {
    lost: 0,
    entries: ASKED.slice(0, shown).map((prompt, i) => {
      const done = i + 1 < shown || looks > ASKED.length;
      return {
        job,
        n: i + 1,
        at: base + i * 20,
        service: "Anthropic",
        model: "smaller model",
        step: `reading part ${i + 1} of 3`,
        prompt,
        reply: done ? BACK[i]! : null,
        state: done ? ("done" as const) : ("waiting" as const),
        reason: null,
      };
    }),
  };
}
