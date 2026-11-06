// The words of "Search by meaning" on the Names page (#2733), in their own file because the box is loaded only
// when it is used (LazyPanel): the switch NAMES_SMART_LIVE stays in names-words.ts. Plain words; DMbot finds
// names close in meaning and the DM decides what to do with them.
export const smartWords = {
  label: "Search by meaning",
  region: "Search names by meaning",
  hint: "Describe it in your own words, like “ice goddess” or “friend of Brannoc”. Use Find a name when you know part of the name. Press Search. It can take a few seconds.",
  typeSome: "Type a few words first.",
  showing: "Showing the closest names. Press Back to all names, or use Find a name or the types, to see the list again.",
  button: "Search",
  // The stage shown while it works (counts and stages only: never the words typed).
  stage: "Looking for names that mean this",
  capped: "Only your newest notes were searched.",
  found: (n: number): string => (n === 1 ? "1 name found, the closest match." : `${n} names found, the closest first.`),
  none: "No names are close to that. Try other words, or use Find a name.",
  back: "Back to all names",
  notReady: "Search by meaning isn't ready for this campaign yet. Use Find a name for now.",
  failed: "That search didn't work. Press Search to try again, or use Find a name.",
  tooLong: "Use 100 characters or fewer.",
  quiet: "Nothing new for a while. You can keep waiting, or press Search to try again.",
} as const;
