// The words for "Names fixed this session" (#2645): what the name pass wrote into a session's cleaned
// text, for the campaign's DM only, with an Undo for each name. Switched off until CloudDev's routes
// (#2684) are deployed. Plain words; the pass fixes only what PlotBot was sure of, and the
// DM can take any of it back. The "heard as" spellings are transcript words, shown to the DM only.
export const NAMES_FIXED_LIVE = false; // moves with the What's new entry "Names fixed this session, with Undo"

export const namesFixedWords = {
  heading: "Names fixed this session",
  lead: "DMbot fixed these spellings in the cleaned transcript. It only fixed names it was sure of. Press Undo to put a name back as it was heard. Only you can see this.",
  loading: "Looking for fixed names…",
  empty: "DMbot didn't fix any names in this session. Nothing to check here.",
  times: (n: number): string => (n === 1 ? "fixed once" : `fixed ${n} times`),
  heardAs: "It was written as",
  heardTimes: (heard: string, n: number): string => (n === 1 ? `${heard}, once` : `${heard}, ${n} times`),
  undo: "Undo",
  undoName: (name: string): string => `Undo ${name}`,
  // The wait (owner rule 2026-10-23). The bot gives no stages for an Undo, so the page shows how long it has
  // waited and, after a while, says plainly that it is not saved yet.
  working: (name: string, seconds: number): string => `Putting ${name} back… ${seconds} seconds so far.`,
  slow: "Still working. This isn't saved yet. Please wait; there is no need to press Undo again.",
  undone: "Put back as it was heard.",
  putBack: (n: number): string => (n === 1 ? "Done. 1 place put back. DMbot will leave this spelling alone from now on." : `Done. ${n} places put back. DMbot will leave this spelling alone from now on.`),
  nothingBack: "Nothing to put back. This name was already undone, or the transcript changed since.",
  stillChecking: (unsure: number, fresh: number): string => {
    const parts: string[] = [];
    if (unsure) parts.push(unsure === 1 ? "1 word it wasn't sure of" : `${unsure} words it wasn't sure of`);
    if (fresh) parts.push(fresh === 1 ? "1 new name" : `${fresh} new names`);
    return parts.length ? `DMbot left ${parts.join(" and ")} for you to decide. They are under Names to check on this page.` : "";
  },
  errors: {
    gone: "Nothing to put back. This name was already undone, or the transcript changed since. The list now shows the newest.",
    "cannot-write": "DMbot can't save that right now. Nothing was changed. Try again in a minute.",
    "queue-full": "Too many changes are waiting. Give DMbot a moment, then try again.",
    "too-soon": "That was a bit quick. Wait a few seconds, then try again.",
    "not-allowed": "Only this campaign's DM can see this.",
    "still-waiting": "Not saved yet. The list shows how it stands now. Wait a minute before you press Undo again.",
    network: "Couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Nothing was changed. Try again in a minute.",
  } as Record<string, string>,
};
