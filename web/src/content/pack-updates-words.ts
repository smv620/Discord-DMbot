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
};
