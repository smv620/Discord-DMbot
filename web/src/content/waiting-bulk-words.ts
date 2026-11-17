// The words of "All true" and "All secret" above the list of what was said at the table (#3645), the same as Discord's
// buttons (✅ All true, 🤫 All secret, ↩️ Undo all N), with the count each press will answer. Plain words. DMbot never
// decides: the game master presses, and each button answers only what is shown and still waiting.
const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

export const bulkWords = {
  allTrue: (n: number): { icon: string; text: string } => ({ icon: "✅", text: `All true (${n})` }),
  allSecret: (n: number): { icon: string; text: string } => ({ icon: "🤫", text: `All secret (${n})` }),
  undo: (n: number): { icon: string; text: string } => ({ icon: "↩️", text: `Undo all ${n}` }),
  hint: "Each button answers everything shown below that is still waiting. You can undo the last press.",
  hintSecret: "A fact that has changed, like \"Roan is dead\", can't be secret, so All secret leaves it for you.",
  stage: "Saving your answers.",
  unit: "answers",
  markedTrue: (n: number): string => `You marked ${n} as true.`,
  markedSecret: (n: number): string => `You marked ${n} as secret.`,
  shownMany: (n: number): string =>
    n === 1
      ? "👁️ 1 of them was said at the table, so your players can see it now."
      : `👁️ ${n} of them were said at the table, so your players can see them now.`,
  heldMany: (n: number): string =>
    n === 1
      ? "🙈 1 of them is kept hidden from your players, so it can't give away another hidden connection between the same two names. To show it, use “👁️ Reveal to players” on the Story Graph."
      : `🙈 ${n} of them are kept hidden from your players, so they can't give away another hidden connection between the same two names. To show them, use “👁️ Reveal to players” on the Story Graph.`,
  skipped: (n: number): string => `${plural(n, "fact", "facts")} left for you, because they can't be secret.`,
  sceneMany: (n: number): string =>
    n === 1
      ? "👁️ 1 of them was said while the party was split: only the characters who were there know it."
      : `👁️ ${n} of them were said while the party was split: only the characters who were there know them.`,
  splitMany: (n: number): string =>
    n === 1
      ? "🙈 1 of them is kept hidden from your players: the party was split, and DMbot can't tell which group heard it."
      : `🙈 ${n} of them are kept hidden from your players: the party was split, and DMbot can't tell which group heard them.`,
  refused: (n: number, why: string): string => `${plural(n, "answer", "answers")} could not be saved: ${why}`,
  stoppedEarly: (n: number): string => `Stopped early: ${n} not tried. Check your connection and press again.`,
  noUndo: "This press can't be undone. Change each answer by hand if you need to.",
  undid: (n: number): string => `Undid ${n}. DMbot will ask about them again.`,
  couldNotUndo: (n: number): string => `${n} could not be undone because they changed since. Check them in the list.`,
  fallbackReason: "Something went wrong. Try again.",
} as const;
