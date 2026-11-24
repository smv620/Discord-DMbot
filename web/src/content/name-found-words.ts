// The words of the new name card (#5144): Discord's own (`memory/name_words.py`, `ui/names.py`), so both places say the same.
// The heard line comes from core as plain text (`found.heard`), so this page never rebuilds it. The line that made it a name is a
// quote of the table, shown only when core sends it (its speaker still agrees to be recorded).
export const nameFoundWords = {
  /** DMbot's guess, under the title: the kind's own label ("a place" is said by the page from the list's kinds). */
  guess: (kindLabel: string): string => `DMbot's guess: ${kindLabel.toLowerCase()}.`,
  /** The line that made it a name: the speaker and the words, exactly as heard. */
  lineLabel: "What made it a name:",
  line: (speaker: string, text: string): string => `${speaker}: “${text}”`,
  /** The yes when DMbot has a guess: spelled as the card says it. */
  yesSpelled: (name: string): string => `✅ Yes, spelled ${name}`,
  yesSpelledFor: (name: string): string => `Yes, spelled ${name}`,
  /** The same yes for a long name, as Discord says it. */
  yesSpelling: "✅ Yes, this spelling",
  /** Names longer than this get the short yes. */
  longName: 24,
  /** A thing or place the story keeps coming back to but never names (#5175, slice 6): its three answers, for that question only. */
  singled: {
    keepIt: "✅ Keep it",
    giveName: "✏️ Give it a name…",
    notWorth: "🚫 Not worth keeping",
    keepItFor: (name: string): string => `Keep it: ${name}`,
    notWorthFor: (name: string): string => `Not worth keeping: ${name}`,
    giveNameFor: (name: string): string => `Give it a name: ${name}`,
    giveNameLabel: "Its name in your game",
    giveNamePlaceholder: "For example: the Glittering Deep",
    giveNameUse: "Give it a name",
    /** On a kept one's row: it is described in the story and has no name of its own. */
    described: "🔖 Described in the story, no name of its own.",
  },
} as const;
