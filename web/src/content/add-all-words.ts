// The words of "Add all" on the Upload lore page (#3207, #3222). Plain words: the DM adds the names a document
// found, watches the wait, and can take them back. DMbot never adds a name it did not find, and ideas are never counted.
import { kindWord, kindWords } from "./web-words";

export const addAllWords = {
  found: (n: number): string =>
    n === 1 ? "1 name found in your document is waiting for your OK." : `${n} names found in your document are waiting for your OK.`,
  addOne: "Add 1 name",
  addAll: (n: number): string => `Add all ${n} names`,
  addKind: (n: number, kind: string): string => `Add all ${n} ${kind}`,
  addOneKind: (kind: string): string => `Add 1 ${kind}`,
  checkOneByOne: "Check them one by one",
  ideas: "Ideas are not included. Check those yourself.",
  nothingLeft: "Nothing left to add.",
  adding: "Adding names",
  undoing: "Taking the names back",
  part: (done: number, total: number): string => `part ${done} of ${total}`,
  stalled: "Still working, but slower than usual.",
  failedNone: "Adding stopped. No names were added.",
  nothingAdded: "Nothing was added.",
  lostTouch: "Lost touch with DMbot. The names may still be added. Check the Names page to see what was added.",
  undoFailed: "Undo stopped. The names stay added.",
  failedSome: (n: number, total: number): string => `Adding stopped after ${n} of ${total}. The rest are still waiting.`,
  tryAgain: "Try again",
  undo: "Undo",
  undone: (n: number): string => (n === 1 ? "Undone. 1 name is waiting again." : `Undone. ${n} names are waiting again.`),
  added: (n: number, list: string): string => (n === 1 ? `Added 1 name: ${list}.` : `Added ${n} names: ${list}.`),
  addedKind: (n: number, kind: string): string => `Added ${n} ${kind}.`,
  notFound: "This reading is too old to add from. Open the Names page to check them one by one, or upload the document again.",
  openNames: "Open Names page",
  refused: "Only the DM can add names.",
};

/** A kind's plural word, lower case in a sentence ("5 places"); a word that opens with capitals, such as "NPCs", keeps them. */
export function plainKindWord(key: string): string {
  const word = kindWords(key).replace(/_/g, " ");
  return /^[A-Z]{2}/.test(word) ? word : word.toLowerCase();
}

/** A kind's singular in a sentence: "1 place"; capitals that open a word, such as "NPC", stay. */
export function plainKindSingular(key: string): string {
  const word = kindWord(key);
  return /^[A-Z]{2}/.test(word) ? word : word.toLowerCase();
}

/** The biggest kinds first, the two biggest named and the rest counted: "5 places and 7 characters, and 2 other kinds". */
export function countsList(counts: Record<string, number>): string {
  const sorted = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])); // ties by name, so the order never depends on the object's keys
  const named = sorted.slice(0, 2).map(([k, n]) => `${n} ${n === 1 ? plainKindSingular(k) : plainKindWord(k)}`);
  const others = sorted.length - named.length;
  const head = named.join(" and ");
  if (others === 0) return head;
  return others === 1 ? `${head}, and 1 other kind` : `${head}, and ${others} other kinds`;
}

export const sumCounts = (counts: Record<string, number>): number => Object.values(counts).reduce((a, b) => a + b, 0);
