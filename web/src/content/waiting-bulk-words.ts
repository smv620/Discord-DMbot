// The words of "All true" and "All secret" above the list of what was said at the table (#3645), the same as Discord's
// buttons (✅ All true, 🤫 All secret, ↩️ Undo all N). Plain words. DMbot never decides: the game master presses, and each
// button answers only what is shown and still waiting.
const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

export const bulkWords = {
  allTrue: "✅ All true",
  allSecret: "🤫 All secret",
  hintTrue: "All true says each one shown is true.",
  hintSecret: "All secret keeps each one shown secret. A state, like \"Roan is dead\", can't be secret, so it is left for you.",
  stage: "Answering the ones shown.",
  unit: "answers",
  undo: (n: number): string => `↩️ Undo all ${n}`,
  keptTrue: (n: number): string => `Kept ${plural(n, "answer", "answers")} as true.`,
  keptSecret: (n: number): string => `Kept ${plural(n, "answer", "answers")} as secret.`,
  skipped: (n: number): string => `${n} left for you, because a state can't be secret.`,
  refused: (n: number, why: string): string => `${plural(n, "one was", "ones were")} not kept: ${why}`,
  undid: (n: number): string => `Undid ${plural(n, "answer", "answers")}. DMbot will ask about them again.`,
  couldNotUndo: (n: number): string => `${n} could not be undone, because something changed since.`,
} as const;
