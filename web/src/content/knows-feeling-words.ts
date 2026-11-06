// The words of the feeling picker on a "knows (…)" connection (#3607). Plain words; DMbot suggests where a feeling came
// from, the game master decides. The quote is the DM's alone and never reaches a player's view.
/** The nine feelings, worst first (core reputation_words.WORDS): the picker lists them in this order. */
export const FEELINGS = ["sworn enemy", "hostile", "distrusts", "wary", "neutral", "trusts", "friendly", "close", "devoted"] as const;
export type Feeling = (typeof FEELINGS)[number];

/** The feeling in a "knows (feeling)" connection's words, or null for any other connection. */
export function feelingOf(words: string): Feeling | null {
  const m = /^knows \((.+)\)$/.exec(words.trim());
  const word = m?.[1]?.trim().toLowerCase();
  return FEELINGS.find((f) => f === word) ?? null;
}

export const feelingWords = {
  label: (n: number): string => `How they feel, for connection ${n}`,
  save: "Save feeling",
  saveFor: (n: number): string => `Save the feeling for connection ${n}`,
  saved: "Saved. You can undo this until your next change.",
  why: (feeling: string): string => `Why ${feeling}?`,
  from: {
    session: (n: number): string => `From session ${n}.`,
    upload: (label: string, line?: number): string => `From your lore upload ${label.slice(0, 80)}${line ? `, line ${line}` : ""}.`,
    dm: "You set this yourself.",
  },
  noSource: "DMbot doesn't have the line this came from.",
  quoteLabel: "The words it came from, only you see them",
  loading: "Loading…",
} as const;
