// The words of the join question on the Questions page (#4778 part b): the same as Discord's card (ui/party_join.py), with no
// markdown. Names are shown as given, never with a pronoun. DM only.
/** Names joined the way Discord joins them; a name with a comma in it is still one name. */
function joinOf(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export const joinWords = {
  title: "New in the party",
  everyone: "Only what everyone knows",
  party: "What the party knows",
  undo: "↩ Undo",
  undoFor: (names: string[]): string => `Undo the last "What the party knows", for ${joinOf(names)}.`,
  undone: (names: string, one: boolean): string =>
    `↩️ Undone. ${names} ${one ? "knows" : "know"} only what everyone knows again, plus what was said at the table since.`,
  everyoneChosen: "Only what everyone knows is chosen for now.",
  // A character coming back to the party (#4791): core's ui/party_join.py question_text(back=True), with no markdown.
  backEveryone: "Only what was known before",
  backHeading: (names: string[]): string => (names.length === 1 ? `${joinOf(names)} is back in the party.` : `${joinOf(names)} are back in the party.`),
  backQuestion: (names: string[]): string =>
    names.length === 1 ? `What does ${joinOf(names)} know about what happened while away?` : `What do ${joinOf(names)} know about what happened while away?`,
  backExplain: (names: string[]): string =>
    names.length === 1
      ? `Until you choose, ${joinOf(names)} knows only what was known before leaving, plus what's said at the table from now on.`
      : `Until you choose, ${joinOf(names)} know only what each knew before leaving, plus what's said at the table from now on.`,
  backEveryoneDone: (names: string[]): string =>
    names.length === 1
      ? `✅ ${joinOf(names)} knows only what was known before leaving, plus what's said at the table from now on.`
      : `✅ ${joinOf(names)} know only what each knew before leaving, plus what's said at the table from now on.`,
  backUndone: (names: string[]): string =>
    names.length === 1
      ? `↩️ Undone. ${joinOf(names)} knows only what ${joinOf(names)} knew before leaving, plus what was said at the table since.`
      : `↩️ Undone. ${joinOf(names)} know only what each knew before leaving, plus what was said at the table since.`,
  later: (names: string): string => `You can tell ${names} more later from any card.`,
  saving: "Working on it…",
  nothingToGive: (names: string): string => `✅ The party doesn't know anything yet, so there was nothing to give ${names}.`,
  everyoneDone: (names: string, one: boolean): string =>
    `✅ ${names} ${one ? "knows" : "know"} only what everyone knows, plus what's said at the table from now on.`,
  partyDone: (names: string, given: number, one: boolean): string =>
    `✅ ${names} now ${one ? "knows" : "know"} the ${given} ${given === 1 ? "thing" : "things"} the party knew.`,
  gone: "Already answered (by you or another DM). Nothing changed.",
  tooLate: "Can't undo this one: some of it changed since. Open the character's card to fix it.",
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  loading: "Loading…",
  failed: "Couldn't load this.",
  tryAgain: "Try again",
  /** Names joined the way Discord joins them: "Mira", "Mira and Tavin", "Mira, Tavin and Oskar". */
  join: (names: string[]): string => joinOf(names),
  heading: (names: string[]): string => (names.length === 1 ? `${joinOf(names)} has joined the party.` : `${joinOf(names)} have joined the party.`),
  question: (names: string[]): string =>
    names.length === 1 ? `What does ${joinOf(names)} know about what came before?` : `What do ${joinOf(names)} know about what came before?`,
  explain: (names: string[]): string =>
    names.length === 1
      ? `Until you choose, ${joinOf(names)} knows only what anyone in the world would know, ${names[0]}'s own backstory, and what's said at the table from now on.`
      : `Until you choose, ${joinOf(names)} know only what anyone in the world would know, each character's own backstory, and what's said at the table from now on.`,
};
