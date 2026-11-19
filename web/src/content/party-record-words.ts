// The words of the party record on a character's card (#4778 a2): core's ui/party_card.py, unchanged; only the date is the page's own.
// Plain text on the page (no markdown). Never a pronoun: the name is shown as given.

const WHY: Record<string, string> = { left: "moved on", retired: "retired", died: "died", away: "away for a while" };

/** The page's own date for a stretch's time (unix seconds). */
export function dayOf(seconds: number): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" }).format(new Date(seconds * 1000));
}

export const partyRecordWords = {
  heading: "🧭 Party record",
  open: "🧭 Party record",
  leaveButton: "🚪 Has left the party",
  backButton: "🤝 Back in the party",
  undo: "↩️ Undo",
  why: (name: string): string => `Why did ${name} leave the party?`,
  nothingForgotten: (name: string): string => `Nothing ${name} knew is forgotten.`,
  reasons: { left: "Moved on", retired: "Retired", died: "Died", away: "Away for a while" },
  since: (day: string, session: number | null): string =>
    session === null ? `🧭 In the party since ${day}.` : `🧭 In the party since session ${session} (${day}).`,
  leftOn: (day: string, ended: string | null): string => `🧭 Left the party on ${day} (${WHY[ended ?? ""] ?? "moved on"}).`,
  leftDone: (name: string, ended: string): string =>
    `✅ ${name} has left the party (${WHY[ended] ?? "moved on"}). Nothing ${name} knew is forgotten. Answers to the table now count only who is still in the party.`,
  backDone: (name: string, asked: "back" | "join" | "none"): string =>
    asked === "back"
      ? `✅ ${name} is back in the party and still knows everything from before. DMbot will ask what ${name} knows of the time away, never during play.`
      : asked === "join"
        ? `✅ ${name} is back in the party. DMbot will still ask what ${name} knows about what came before, never during play.`
        : `✅ ${name} is back in the party and still knows everything from before.`,
  undoneLeave: (name: string): string => `↩️ Undone. ${name} is in the party again.`,
  undoneBack: (name: string): string => `↩️ Undone. ${name} is out of the party again.`,
  tooLateLeave: "Too late to undo this one. Nothing changed.",
  tooLateBack: (name: string): string => `Too late to undo this one. Nothing changed. Use 🚪 Has left the party on ${name}'s card instead.`,
  alreadyBack: (name: string): string => `${name} is already back in the party. Nothing changed.`,
  notInParty: (name: string): string => `${name} isn't in the party now, so nothing changed.`,
  notAPc: (name: string): string => `${name} isn't a player character now, so nothing changed.`,
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  loading: "Loading…",
  saving: "Working on it…",
  failed: "Couldn't load the party record.",
};
