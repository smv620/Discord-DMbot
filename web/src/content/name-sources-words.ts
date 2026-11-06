// The Names page's "Partial campaign" filter (#3675), in plain words. A source is a session or a piece of
// uploaded lore; only names found in the ones ticked are listed.
export const sourceWords = {
  label: "Show names from",
  full: "Full campaign",
  partial: "Partial campaign",
  legend: "Sessions and lore",
  hint: "Tick the sessions and lore to look at. Only names found in them are listed.",
  none: "Nothing ticked yet, so every name is listed.",
  loading: "Loading the sessions and lore…",
  failed: "The sessions and lore didn't load. Choose Full campaign, or reload the page.",
  empty: "This campaign has no sessions or lore yet.",
  session: "Session",
  lore: "Lore",
  clear: "Untick all",
} as const;
