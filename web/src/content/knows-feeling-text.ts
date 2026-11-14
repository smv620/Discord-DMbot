// The words of the feeling picker on a "knows (…)" connection (#3607). Plain words; DMbot suggests where a feeling came
// from, the game master decides. The quote is the DM's alone and never reaches a player's view.
export const feelingWords = {
  label: (n: number): string => `How they feel, worst to best, for connection ${n}`,
  notSaved: "Not saved yet. Press Save feeling.",
  saving: "Saving…",
  quoteCaption: "Only you can see this quote.",
  noQuote: "DMbot kept where this came from, but not the words.",
  save: "Save feeling",
  saveFor: (n: number): string => `Save the feeling for connection ${n}`,
  saved: "Saved. You can undo this until your next change.",
  why: (feeling: string): string => `Why ${feeling}?`,
  from: {
    session: (n: number): string => `From session ${n}.`,
    upload: (label: string, line?: number): string => `From your lore upload ${label.length > 80 ? `${label.slice(0, 80)}…` : label}${line ? `, line ${line}` : ""}.`,
    dm: "You set this yourself.",
  },
  noSource: "DMbot doesn't have the line this came from. You can still change the feeling.",
  quoteLabel: "The words it came from, only you see them",
} as const;
