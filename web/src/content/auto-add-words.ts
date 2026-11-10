// The words of the notice after a lore upload or an import, Undo this upload, and the "Ask me before adding" setting (#3879,
// website half of #3878; the Discord twin is core #3887). Plain words. Counts and labels only: never a name or a line.
import { countsList } from "./add-all-words";

export const autoAddWords = {
  applying: "Adding what DMbot is sure of",
  // "Added 62 names (30 NPCs, 24 places, …) and 48 connections from Lore, 24 Oct."
  added: (names: number, byKind: Record<string, number>, connections: number, label: string): string => {
    const kinds = Object.values(byKind).some((n) => n > 0) ? ` (${countsList(byKind)})` : "";
    const nameWord = `${names} ${names === 1 ? "name" : "names"}${kinds}`;
    const linkWord = `${connections} ${connections === 1 ? "connection" : "connections"}`;
    const from = label ? ` from ${label}` : "";
    if (names > 0 && connections > 0) return `Added ${nameWord} and ${linkWord}${from}.`;
    if (names > 0) return `Added ${nameWord}${from}.`;
    if (connections > 0) return `Added ${linkWord}${from}.`;
    return `DMbot was not sure of anything to add${from}.`;
  },
  seeNames: "Names added by this upload",
  seeGraph: "Story Graph",
  questions: (n: number): string => `${n} ${n === 1 ? "question needs" : "questions need"} you.`,
  questionsLink: "Answer them on the Questions page",
  undo: "Undo this upload",
  undoUntil: (days: number): string => `You can undo it for ${days} ${days === 1 ? "day" : "days"}.`,
  undoAsk: "Take back everything this upload added? Names and connections that something since depends on stay.",
  undoYes: "Yes, undo this upload",
  undoNo: "No, keep it",
  undoing: "Taking it back",
  tookBack: (took: number, of: number): string => `Took back ${took} of ${of}.`,
  undoneAlready: "This upload has already been undone.",
  undoLate: "This upload can't be undone any more. It was more than 7 days ago.",
  notice: { failed: "DMbot couldn't read how this upload went. Open the Names page to see what was added.", stalled: "Nothing new for a while." },
  setting: {
    heading: "Ask me before adding",
    on: "On: after an upload, DMbot asks before it adds names and connections.",
    off: "Off: after an upload, DMbot adds what it is sure of by itself and asks about the rest. You can undo an upload for 7 days.",
    toggleOn: "Turn on Ask me before adding",
    toggleOff: "Turn off Ask me before adding",
    saved: "Saved.",
  },
} as const;
