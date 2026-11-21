// The words of the Plot threads panel on the Names page (#3696, slice 1): read only, DM only. The state words, the history lines, the roles
// and the stalled line come from core in the answer, so this file holds only the labels the page says around them, worded as Discord's card.
export const plotThreadsWords = {
  heading: "Plot threads",
  intro: "The threads DMbot keeps for your campaign. Only you see this.",
  loading: "Loading…",
  slow: "Still loading. This is taking longer than usual.",
  failed: "Couldn't load the threads just now. Nothing changed. Try again in a moment.",
  tryAgain: "Try again",
  none: "No threads yet. Add one from a name's card in Discord.",
  noState: "No state yet",
  stalled: "Stalled",
  // Singular for one session: "Quiet for 1 session".
  quiet: (n: number): string => `Quiet for ${n} ${n === 1 ? "session" : "sessions"}`,
  // Used only when the answer does not send the line ready-made (stalledLine).
  stalledLine: (n: number, after: number): string =>
    n === 1 ? `1 thread hasn't moved for ${after} or more sessions.` : `${n} threads haven't moved for ${after} or more sessions.`,
  involved: "Involved:",
  movedBy: "Moved by:",
  endedIt: (name: string): string => `${name} (ended it)`,
  cameFrom: "Came from:",
  ledTo: "Led to:",
  madeBy: "Made by:",
  madeTo: "Made to:",
  due: "Due:",
  more: (n: number): string => `+${n} more`,
  seeHistory: "See history",
  hideHistory: "Hide history",
  showing: (total: number): string => `Showing the first 100 of ${total} threads.`,
  person: (name: string, role: string): string => (role ? `${name} (${role})` : name),
  listLabel: "Plot threads list",
};
