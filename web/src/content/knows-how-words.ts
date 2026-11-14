// How two people know each other, on a "knows" connection (#3882, website half of #3881; the words are Supervisor's, final):
// "has met", "has heard of", and plain "knows" when the story does not say. Not "is aware of". The DM's feeling word stays
// after it ("has met (wary)"). Kept in a small file with the reader so the Story Graph's page loads only this.
export type KnowsHow = "met" | "aware" | "";

/** The words a `knows` connection starts with, by how: what core puts first in the connection's words. */
export const KNOWS_LEAD: Record<KnowsHow, string> = { met: "has met", aware: "has heard of", "": "knows" };

/** How the two know each other, read out of a connection's words ("has met (wary)" is "met"); null when the connection is
 * not a `knows` at all. The words are core's: this only reads them. */
export function knowsHowOf(words: string): KnowsHow | null {
  const m = /^(knows|has met|has heard of)(?: \(.+\))?$/.exec(words.trim());
  if (!m) return null;
  return m[1] === "has met" ? "met" : m[1] === "has heard of" ? "aware" : "";
}

/** The connection's words with how they know each other changed, the feeling kept ("knows (wary)" to "has met (wary)"). */
export function withKnowsHow(words: string, how: KnowsHow): string {
  const rest = /^(?:knows|has met|has heard of)(.*)$/.exec(words.trim())?.[1] ?? "";
  return `${KNOWS_LEAD[how]}${rest}`;
}

export const knowsHowWords = {
  label: (n: number): string => `How they know each other, connection ${n}`,
  options: { met: "Yes, they've met", aware: "No, only heard of them", "": "Not said if they've met" } as Record<KnowsHow, string>,
  save: "Save",
  saveFor: (n: number): string => `Save how they know each other for connection ${n}`,
  saved: "Saved. You can undo this until your next change.",
  saving: "Saving…",
  loading: "Loading…",
} as const;
