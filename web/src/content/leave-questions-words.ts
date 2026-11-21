// The words of the "Still in the party?" question on the Questions page (#4895, party knowledge 6a): core's ui/party_leave.py words, with
// no markdown. A name is the character's own, never a pronoun. DM only.

// A name longer than this is cut in a button, as core cuts it (party_leave.py SHORT_NAME): the cut name, ellipsis included, is this long.
const SHORT = 16;

/** The name as a button shows it: cut to sixteen characters with "…" when it is longer. */
export function shortName(name: string): string {
  const chars = [...name];
  return chars.length > SHORT ? `${chars.slice(0, SHORT - 1).join("")}…` : name;
}

export const leaveWords = {
  title: "Still in the party?",
  loading: "Loading…",
  failed: "Couldn't load the questions.",
  tryAgain: "Try again",
  died: (name: string): string => `${name} is marked dead. Mark ${name} as no longer in the party?`,
  leave: (name: string): string => `${name} said goodbye at the table. Has ${name} left the party?`,
  left: "Left the party",
  stays: "Still in the party",
  shortLeft: (name: string): string => `${shortName(name)}: left`,
  shortStays: (name: string): string => `${shortName(name)}: stays`,
  later: "Later",
  laterDone: "Kept for later in Questions for you.",
  leftDone: (name: string): string => `✅ ${name} has left the party. DMbot keeps what ${name} knew, in case ${name} comes back.`,
  stayedDone: (name: string): string => `✅ ${name} is still in the party.`,
  undo: "↩️ Undo",
  undone: (name: string): string => `↩️ Undone. ${name} is in the party again.`,
  gone: "Already answered (by you or another DM). Nothing changed.",
  tooLate: (name: string): string => `Can't undo this one: something changed since. Open ${name}'s card to put ${name} back in the party.`,
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  // The bot may still have made the change when the wait ran out: say so, and do not say nothing changed.
  unsure: "Not sure it went through. The list below shows what is still waiting.",
  refreshFailed: "Couldn't read the list again. What is shown may be out of date.",
  saving: "Working on it…",
};
