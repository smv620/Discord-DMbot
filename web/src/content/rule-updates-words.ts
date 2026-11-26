// The words of the "See changes" and "Pick changes" card under a followed house-rules set (#4841 C1–C2, #4845, #4855). The change
// lines are core's own words and are shown as they come; these are the page's plain words around them. The page never takes an
// update by itself: DMbot takes it at the next session start, never during a game.
const changes = (n: number): string => (n === 1 ? "change" : "changes");

export const ruleUpdateWords = {
  newer: (name: string, to: number, from: number): string => `A newer version of ${name} is waiting: version ${to} (you follow version ${from}).`,
  takesWhen: "DMbot takes this update when your next session starts, never during a game.",
  seeChanges: "See changes",
  hideChanges: "Hide changes",
  changes: (words: string): string => `It changes ${words}:`,
  more: (n: number): string => `…and ${n} more.`,
  failed: "Couldn't load the changes.",
  pick: "Pick changes",
  // The legend of the picker's group: the intro's first sentence (#4884).
  pickLegend: "Pick the changes to keep.",
  pickIntro: (name: string): string =>
    `Pick the changes to keep. Leaving anything out ends your follow of ${name}: your table keeps its own copy of the rules, with the changes you kept, and stops getting this set's updates. You can undo it for a day.`,
  save: "Save my picks",
  neverMind: "Never mind",
  done: (name: string, kept: number, left: number): string =>
    `✅ Done. ${name} is now your own copy: you kept ${kept} ${changes(kept)} and left out ${left} ${changes(left)}. You no longer get its updates. Your own rules still win.`,
  undo: "↩️ Undo",
  undoLine: "You can undo this for a day.",
  undone: (from: number): string =>
    `↩️ Undone. Back on version ${from} and following it again. DMbot will take the next new version; use Pick changes to choose.`,
  changed: "The set changed again while you were looking. Look at the new changes first.",
  gone: "This campaign isn't following a set of house rules, or there is nothing new to take.",
  noRoom: "Your own house rules and this set together are more than the limit. Remove some first.",
  tooMany: "This update has more changes than can be listed here. Use Pick changes in Discord.",
  tooLate: "Too late to undo this one. Nothing changed.",
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  // An update DMbot already took, and its Undo (#4867).
  taken: (name: string, to: number): string => `${name} house rules were updated to version ${to}. DMbot took the new version.`,
  takenChanges: "Changes:",
  undoTakenLine: (from: number): string => `Undo goes back to version ${from}. DMbot will take the next new version.`,
  tooLateTaken: "Can't undo this one. It was already undone, or DMbot has since taken a newer version. Nothing changed.",
};
