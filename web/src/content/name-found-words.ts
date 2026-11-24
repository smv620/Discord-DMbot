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
  somethingElse: "Something else",
} as const;
