// The words of "Let me choose" on the join question (#4841, #4799 slice 2, the page half of #4891): core's words from
// `ui/party_choose.py`, with the markdown taken out. The names are joined as the join card joins them; a count is "1 thing" or "N things".
import { joinWords } from "./join-questions-words";

/** A count in words: "1 thing" or "14 things". */
const things = (n: number): string => (n === 1 ? "1 thing" : `${n} things`);

export const joinChooseWords = {
  button: "Let me choose",
  close: "Close",
  lookAgain: "Look again",
  working: "DMbot is working out what each session and story thread would give.",
  stillWorking: "Still working. This can take a while.",
  secondsSoFar: (s: number): string => (s === 1 ? "1 second so far." : `${s} seconds so far.`),
  // A re-read after a pick that failed: the list stays, and this says it may be out of date (#4891).
  staleCounts: "These counts may be out of date. Press Look again.",
  workedOut: (ago: string): string => `Worked out ${ago} ago.`,
  failed: "Couldn't work it out just now. Nothing changed. Try again in a moment.",
  head: (who: string, know: string): string => `Choose what ${who} ${know}. Nothing changes until you pick.`,
  untilOne: (who: string): string => `Until then ${who} knows only what everyone knows.`,
  untilMany: "Until then each knows only what everyone knows.",
  untilBackOne: (who: string): string => `Until then ${who} knows only what was known before leaving.`,
  untilBackMany: "Until then each knows only what each knew before leaving.",
  each: (who: string): string =>
    `Each pick gives ${who} what the rest of the party knows from that session or story thread, with its own ↩️ Undo.`,
  lore: "Uploaded lore and notes you typed in aren't part of any session. Find those by story thread, or use 👁️ Who knows this? on any card.",
  // Core's ONLY_LAST, copied exactly (ui/party_join.py).
  onlyLast: "Showing the last 25 sessions.",
  threadsCut: "Showing the 25 story threads with the most to give. For the rest, use 👁️ Who knows this? on any card.",
  nobody: (who: string): string =>
    `No one else in the party is caught up yet, so there's nothing to give ${who} yet. Answer the other characters' questions first.`,
  nothingAtAll: (who: string, know: string): string =>
    `There's nothing left to choose: ${who} ${know} everything the rest of the party knows from the sessions and story threads. To give something else, use 👁️ Who knows this? on it.`,
  bySession: "Catch up by session…",
  byThread: "Catch up by story thread…",
  sessionLabel: (n: number): string => `Session ${n}`,
  give: "Give",
  countOne: (n: number, name: string): string => `${things(n)} ${name} doesn't know yet`,
  countMany: (parts: string): string => parts,
  // A pick's done line: one character, or several (the first's count, then the others').
  doneOne: (name: string, n: number, source: string): string =>
    `✅ ${name} now knows ${things(n)} the rest of the party knew from ${source}. Nothing was sent to ${name}'s player.`,
  // The first character of several, as the done line starts: "Mira now knows 14 more things".
  doneManyFirst: (name: string, n: number): string => `${name} now knows ${n} more ${n === 1 ? "thing" : "things"}`,
  // A pick whose names match no character, or that gave only to characters not named: nothing is claimed about who knows what.
  doneGeneric: "✅ Done. Nothing was sent to the players.",
  doneMany: (first: string, rest: string[], source: string): string =>
    `✅ From ${source}, ${first}${rest.length ? ` and ${rest.join(" and ")}` : ""}. Nothing was sent to the players.`,
  moreGiven: (n: number): string => `${n} more ${n === 1 ? "character was" : "characters were"} given things too.`,
  undo: "↩️ Undo",
  undoneFirst: (who: string): string => `↩️ Undone. ${who} knows only what everyone knows again, plus what was said at the table since.`,
  undonePick: (who: string, know: string): string => `↩️ Undone. ${who} no longer ${know} what that pick gave.`,
  nothingLeft: (who: string, know: string, source: string): string =>
    `${who} ${know === "knows" ? "already knows" : "already know"} everything the rest of the party knows from ${source} (another DM may have given it). Nothing changed.`,
  answered: "Already answered (by you or another DM). Nothing changed.",
  tooLate: joinWords.tooLate,
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  saving: "Saving…",
  seconds: (s: number): string => `${s} seconds`,
  minutesAgo: (m: number): string => (m < 1 ? "under a minute" : m === 1 ? "1 minute" : `${m} minutes`),
  // The join's names, as its card says them (the words file of the join question).
  join: (names: string[]): string => joinWords.join(names),
};
