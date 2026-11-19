// The words of "Catch up" on a name's row (#4793): core's ui/party_tell.py, with no markdown. `{name}` is the member's name, never a
// pronoun; `things` is "1 thing" or "N things". Only the DM sees these.

const things = (n: number): string => (n === 1 ? "1 thing" : `${n} things`);

/** A button's label is cut at about this many characters (phones cut it there): core `party_tell.LABEL_MAX`. */
const LABEL_MAX = 25;

/** A name cut to `room` characters with "…", whole when it fits: core `party_tell._cut` (counted in characters, as Python does). */
export function cut(name: string, room: number): string {
  const chars = Array.from(name);
  return chars.length <= room ? name : `${chars.slice(0, Math.max(1, room - 1)).join("").trimEnd()}…`;
}

/** "A", "A and B", "A, B and C". */
export function listOf(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export const catchUpWords = {
  one: (name: string, n: number): string => `${name} is missing ${things(n)} on this card that the rest of the party knows.`,
  many: (names: string[]): string => `${listOf(names)} are missing things on this card that the rest of the party knows.`,
  oneLabel: (name: string): string => `📖 Catch up: ${cut(name, LABEL_MAX - Array.from("📖 Catch up: ").length)}`,
  manyLabel: "📖 Catch up…",
  moreOne: "1 more is missing things on this card too. Catch these up first and it will show.",
  moreMany: (n: number): string => `${n} more are missing things on this card too. Catch these up first and they will show.`,
  pickQuestion: "Catch up who? Each gets everything on this card the rest of the party knows.",
  // "Mira (6 things)": the name is cut, never the count (core `party_tell.pick_label`)
  pickLabel: (name: string, n: number): string => `${cut(name, Math.max(LABEL_MAX - ` (${things(n)})`.length, 8))} (${things(n)})`,
  back: "↩️ Back",
  done: (name: string, n: number): string =>
    `✅ ${name} now knows the ${things(n)} on this card the rest of the party knew. Nothing was sent to ${name}'s player.`,
  undo: "↩️ Undo",
  undone: (name: string, n: number): string => `↩️ Undone. ${name} no longer knows those ${things(n)}.`,
  tooLate: (name: string): string =>
    `Too late to undo this one: some of it has changed since. To take something back from ${name}, use 👁️ Who knows this? on it.`,
  nothingLeft: (name: string): string => `${name} already knows everything the rest of the party knows here (another DM may have done it). Nothing changed.`,
  notInParty: (name: string): string => `${name} isn't in the party now, so nothing changed.`,
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  saving: "Working on it…",
  failed: "Couldn't load the catch-up.",
  nothing: "Nothing to catch up on this card.",
};
