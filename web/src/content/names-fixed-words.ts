// The words for "Names fixed this session" (#2645): what the name pass wrote into a session's cleaned
// text, for the campaign's DM only, with an Undo for each name. Switched off until CloudDev's routes
// (#2684) are deployed and seen working. Plain words; the pass fixes only what PlotBot was sure of, and the
// DM can take any of it back. The "heard as" spellings are transcript words, shown to the DM only.
export const NAMES_FIXED_LIVE = false; // moves with the What's new entry "Names fixed this session, with Undo"

export const namesFixedWords = {
  heading: "Names fixed this session",
  lead: "DMbot corrected these spellings in the cleaned transcript, only where it was sure. Undo puts a name back as it was heard. Only you can see this.",
  empty: "DMbot didn't need to fix any names in this session.",
  times: (n: number): string => (n === 1 ? "corrected once" : `corrected ${n} times`),
  heardAs: "Heard as",
  heardTimes: (heard: string, n: number): string => `${heard} (${n})`,
  undo: "Undo",
  undoName: (name: string): string => `Undo ${name}`,
  working: "Putting the spellings back…",
  undone: "Put back as it was heard.",
  putBack: (n: number): string => (n === 1 ? "Put back 1 line. DMbot won't fix this spelling the same way again." : `Put back ${n} lines. DMbot won't fix this spelling the same way again.`),
  nothingBack: "Nothing to put back: the lines were changed since, or this was undone already.",
  stillChecking: (unsure: number, fresh: number): string => {
    const parts: string[] = [];
    if (unsure) parts.push(unsure === 1 ? "1 word it wasn't sure of" : `${unsure} words it wasn't sure of`);
    if (fresh) parts.push(fresh === 1 ? "1 new name" : `${fresh} new names`);
    return parts.length ? `DMbot left ${parts.join(" and ")} for you to check. They are in Names to check.` : "";
  },
  errors: {
    gone: "Nothing to put back: the lines were changed since, or this was undone already.",
    "cannot-write": "DMbot can't save that right now. Nothing was changed. Try again in a minute.",
    "queue-full": "Too many changes are waiting. Give DMbot a moment, then try again.",
    "not-allowed": "Only this campaign's DM can see this.",
    "not-found": "That list wasn't found.",
    "still-waiting": "Not saved yet. Wait a minute and look at this list again before you press Undo again.",
    network: "Couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Nothing was changed. Try again in a minute.",
  } as Record<string, string>,
};
