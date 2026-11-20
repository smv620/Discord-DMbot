// The words of the "See changes" card under a followed house-rules set (#4841 C1, #4845). The change lines are core's own
// words and are shown as they come; these are the page's plain words around them. The page never takes an update: DMbot
// takes it at the next session start, never during a game.
export const ruleUpdateWords = {
  newer: (name: string, to: number, from: number): string => `A newer version of ${name} is waiting: version ${to} (you follow version ${from}).`,
  takesWhen: "DMbot takes this update when your next session starts, never during a game.",
  seeChanges: "See changes",
  hideChanges: "Hide changes",
  changes: (words: string): string => `It changes ${words}:`,
  more: (n: number): string => `…and ${n} more.`,
  failed: "Couldn't load the changes.",
};
