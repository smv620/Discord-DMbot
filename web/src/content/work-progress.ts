// The words every wait on the website shares (#2565; owner rule 2026-10-23, CLAUDE.md "Every wait shows
// that DMbot is still working"): the stage in plain words, how far along it is, how long it has been
// running and when it last moved forward. A stall says so and says what to do; a failure says what
// happened and what to do next. Counts and stages only, never a word of the person's documents. Each page
// keeps its own list of stage words (from the API's `stage` code); this file holds the rest, and the
// fallback for a stage code the page does not know.
export const workWords = {
  /** For a stage code the page doesn't know (a newer API): still says something true. */
  unknownStage: "DMbot is working on it.",
  /** "3 of 10 sessions", "part 1 of 2". */
  progress: (done: number, total: number, unit?: string): string => (unit ? `${done} of ${total} ${unit}` : `${done} of ${total}`),
  /** A count that is an estimate between two real readings (#3534): "about 42 of 100 lines". */
  progressAbout: (done: number, total: number, unit?: string): string => `about ${done} of ${total}${unit ? ` ${unit}` : ""}`,
  /** The running counts line (#3534), one phrase per count the API sends; a code the page doesn't know is left out. */
  countPhrase: {
    words: (n: number): string => `${n.toLocaleString()} ${n === 1 ? "word" : "words"} processed`,
    calls: (n: number): string => `${n.toLocaleString()} ${n === 1 ? "call" : "calls"} made`,
    checks: (n: number): string => `${n.toLocaleString()} ${n === 1 ? "check" : "checks"} completed`,
    names: (n: number): string => `${n.toLocaleString()} ${n === 1 ? "name" : "names"} found`,
  } as Record<string, (n: number) => string>,
  /** A running count that is an estimate between two readings. */
  aboutCount: (phrase: string): string => `about ${phrase}`,
  running: (elapsed: string): string => `Running for ${elapsed}.`,
  lastChange: (ago: string): string => `Last progress ${ago} ago.`,
  justNow: "Last progress just now.",
  noChangeYet: "Hasn't moved forward yet.",
  // Said once when a stall starts (a live region); how long it has been quiet is beside it, not in it.
  stalled: "Nothing new for a while. It may just be slow.",
  quietFor: (ago: string): string => `Quiet for ${ago}.`,
  /** The page stopped looking (it is gone, or an error): no clock runs, and what was last seen stays. */
  stoppedLooking: "The page has stopped checking.",
  /** Said after a stall when the page has nothing more specific to say. */
  stalledDo: "You can leave this page and come back. If nothing moves in 5 more minutes, try again. Still stuck? Ask on the Help page.",
  failed: "That didn't work.",
  failedDo: "Try again. If it fails again, ask on the Help page and say what you were doing.",
  done: "Done.",
  /** The bar for people using a screen reader. */
  barLabel: (stage: string): string => `Progress: ${stage}`,
  /** Seconds as plain words that change slowly enough to read: "45 sec" (to the nearest 5), then whole
   * minutes ("2 min"), then "1 h 3 min". */
  duration: (seconds: number): string => {
    const whole = Math.max(0, Math.floor(seconds));
    if (whole < 60) return `${whole - (whole % 5)} sec`;
    const m = Math.floor(whole / 60);
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    return m % 60 === 0 ? `${h} h` : `${h} h ${m % 60} min`;
  },
};

/** The page's words for a stage code, or the plain fallback. Own properties only: a code is never a key
 * that reaches into the object's prototype. */
export function stageWords(stages: Record<string, string>, code: string | null | undefined): string {
  return code && Object.hasOwn(stages, code) ? (stages[code] as string) : workWords.unknownStage;
}

/** The words of `SavingWait` (#3347): a change DMbot has to apply, with no progress from the bot to show. */
export const savingWait = {
  stage: "Waiting for DMbot to save it.",
  quiet: "DMbot is slow to answer. Your change may still go through: look at the list in a minute before you try again.",
};
