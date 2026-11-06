// The Names page's "Partial campaign" filter (#3675), in plain words. A source is a session or a piece of
// uploaded lore; only names found in the ones ticked are listed.
export const sourceWords = {
  label: "Show names from",
  full: "Full campaign",
  partial: "Partial campaign",
  legend: "Pick sessions and lore",
  hint: (n: number): string => `${n} ticked. Only names found in ${n === 1 ? "it" : "them"} are shown.`,
  none: "Nothing ticked. All names are shown. Tick a session or lore to show only its names.",
  loading: "Loading the sessions and lore…",
  failed: "The sessions and lore didn't load.",
  tryAgain: "Try again",
  empty: "This campaign has no sessions or lore yet.",
  sessions: "Sessions",
  lore: "Lore",
  clear: "Untick all",
} as const;
