// The words of a story thread's clues on the quest sheet (#4625): plain words for a DM. The count is the API's own words; a clue's
// name is only ever text on the page, and no id is shown.
export const threadClueWords = {
  heading: "🔎 Clues",
  empty: "No clues are tied to this thread yet.",
  found: "found",
  notFound: "not found yet",
  falseLead: "false lead",
  falseLeadOn: "Mark as false lead",
  falseLeadOff: "Not a false lead",
  off: "Take off this thread",
  addLabel: "Clue name",
  add: "➕ Add a clue",
  saving: "Saving…",
  added: (name: string): string => `Added “${name}”. It stays hidden from your players until a character finds it.`,
  falseLeadDone: "Marked as a false lead.",
  falseLeadUndone: "Not a false lead any more.",
  offDone: "Taken off this thread.",
  undo: "↩ Undo",
  undone: "Taken back.",
  missing: "That clue or thread is gone. Reload to see where it stands.",
  badAction: "DMbot could not read that. Try different words.",
  failed: "DMbot couldn't save that answer. Nothing changed. Try again in a minute.",
} as const;
