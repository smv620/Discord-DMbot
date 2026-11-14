// The words of the "Line for the players" box on a name (#3568, the website half of #2967), the same as Discord's name card:
// one short line saying what the players know the name is or does, told in recaps and answers only where they can see the
// entry. Separate from the DM's own note, which players never see. DM only. Plain words, no technical terms.
/** The longest a line may be: core's `PLAYERS_TEXT_MAX` (memory/models.py). Keep the two equal. */
export const PLAYERS_LINE_MAX = 120;

export const playersLineWords = {
  /** The label, exactly (the issue's and Discord's). */
  label: "Players will be told this. No secrets.",
  hint: "They read this in recaps and answers. Your own note stays private.",
  count: (n: number): string => `${n} of ${PLAYERS_LINE_MAX}`,
  open: (name: string): string => `Line for the players about ${name}`,
  save: "Save the line",
  saving: "Saving…",
  saved: (text: string): string =>
    `Saved. Players will read this in recaps and answers, only where they can see this entry: “${text}”. Left out if it names something they don't know yet.`,
  removed: "Line for the players removed. Your own note is unchanged.",
  tooLong: `That's over ${PLAYERS_LINE_MAX} letters. Shorten it and save again.`,
  problems: {
    "ticket-too-long": `That's over ${PLAYERS_LINE_MAX} letters. Shorten it and save again.`,
    "bad-name": "A letter in that can't be kept. Take it out and save again.",
    "not-found": "That name changed. Reload the page and try again.",
    "still-waiting": "DMbot is still saving this. Reload the page in a minute to see it.",
    "not-allowed": "Only the DM can write this.",
    server: "That didn't save. Try again. If it keeps failing, ask on the Help page.",
  } as Record<string, string>,
} as const;

/** The line on a name waiting to be checked (#3833): what the players are told, shown before the DM confirms it. */
export const playersKnowWords = {
  label: "Players know:",
  change: "Change it",
  changeFor: (name: string): string => `Change what players know about ${name}`,
} as const;
