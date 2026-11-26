// The words of the packs a campaign follows from other tables, on the Shared rules page (#4841 packs P1, #4872). The change lines are
// core's own and are shown as they come; these are the page's plain words around them. The page never takes an update: DMbot takes
// it at the next session start, never during a game.
export const packUpdateWords = {
  heading: "Homebrew packs it uses from other tables",
  newer: (name: string, to: number, from: number): string => `${name} has a newer version waiting: version ${to} (you follow version ${from}).`,
  takesWhen: "DMbot takes this update when your next session starts, never during a game.",
  taken: (name: string, to: number): string => `${name} was updated to version ${to}. DMbot took the new version.`,
  see: "See changes",
  hide: "Hide changes",
  changes: (words: string): string => `It changes ${words}:`,
  takenChanges: "Changes:",
  more: (n: number): string => `…and ${n} more.`,
  failed: "Couldn't load the changes.",
  undo: "↩️ Undo",
  undoLine: (from: number): string => `Undo goes back to version ${from}. DMbot will take the next new version.`,
  undone: (back: number): string => `↩️ Undone. Back on version ${back}. DMbot will take the next new version; use Pick changes to choose.`,
  tooLate: "Can't undo this one. It was already undone, or DMbot has since taken a newer version. Nothing changed.",
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  // Pick changes on a pack (#4873).
  pick: "Pick changes",
  pickIntro: (name: string): string =>
    `Pick the changes to keep. Leaving anything out ends your follow of ${name}: your table keeps its own copy of the entries, with the changes you kept, as its own homebrew, and stops getting this pack's updates. You can undo it for a day.`,
  save: "Save my picks",
  neverMind: "Never mind",
  done: (name: string, kept: number, left: number): string =>
    `✅ Done. ${name} is now your own homebrew: you kept ${kept} ${kept === 1 ? "change" : "changes"} and left out ${left} ${left === 1 ? "change" : "changes"}. It won't update any more.`,
  skipped: (n: number): string => `${n} ${n === 1 ? "entry was" : "entries were"} already yours, so ${n === 1 ? "it stayed" : "they stayed"} as ${n === 1 ? "it was" : "they were"}.`,
  pickUndoLine: "You can undo this for a day.",
  pickChanged: "The pack changed again while you were looking. Look at the new changes first.",
  pickGone: "This campaign isn't following that pack, or there is nothing new to take.",
  pickNoRoom: "Your own homebrew and this pack together are more than the limit. Remove some first.",
  pickTooMany: "This update has more changes than can be listed here. Use Pick changes in Discord.",
  pickTooLate: "Too late to undo this one. Nothing changed.",
  pickUndone: (from: number): string =>
    `↩️ Undone. Back on version ${from} and following it again. DMbot will take the next new version; use Pick changes to choose.`,
  pickWaiting: "DMbot is taking a while. Look again in a moment before trying again.",
  pickLegend: "Changes to keep",
};
