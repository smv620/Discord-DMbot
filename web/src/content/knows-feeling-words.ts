// The nine feelings and how to read one out of a connection's words (#3607). Kept apart from the picker's sentences
// (knows-feeling-text.ts) so the Story Graph's own page loads only this small list, and the sentences load with the picker.
/** The nine feelings, worst first (core reputation_words.WORDS): the picker lists them in this order. */
export const FEELINGS = ["sworn enemy", "hostile", "distrusts", "wary", "neutral", "trusts", "friendly", "close", "devoted"] as const;
export type Feeling = (typeof FEELINGS)[number];

/** The feeling in a "knows (feeling)" connection's words, or null for any other connection. */
export function feelingOf(words: string): Feeling | null {
  const m = /^knows \((.+)\)$/.exec(words.trim());
  const word = m?.[1]?.trim().toLowerCase();
  return FEELINGS.find((f) => f === word) ?? null;
}

