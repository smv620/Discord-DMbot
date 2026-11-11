// The words of the redesigned connection panel (#4155). Names are only ever text on the page. Plain words: the DM decides, the
// panel only stages what they pick until they press Save.
export const panelWords = {
  heading: (a: string, b: string): string => `${a} and ${b}`,
  add: "+ Add a connection",
  addClose: "Close the form",
  none: "No connections between these two yet. Press + Add a connection.",
  listLabel: "Connections between these two",
  unsaved: "Unsaved changes",
  unsavedShort: "Unsaved",
  // The arrow reads with the names in the order the panel shows them (a first).
  arrow: (a: string, b: string, direction: "a-b" | "b-a" | "both"): string =>
    direction === "a-b" ? `${a} → ${b}` : direction === "b-a" ? `${a} ← ${b}` : `${a} ↔ ${b}`,
  open: (line: string): string => `Open: ${line}`,
  close: "Close",
  words: "Words",
  // The direction toggle shows the state it is in and what pressing it does; the arrow reads with the names in the panel's order.
  directionLabel: "Direction (tap to change)",
  directionSays: (a: string, b: string, direction: "a-b" | "b-a" | "both"): string =>
    direction === "a-b" ? `${a} to ${b} only.` : direction === "b-a" ? `${b} to ${a} only.` : "Both ways.",
  direction: (says: string): string => `Direction: ${says} Press to change it.`,
  directionNext: "Tap again for the next choice.",
  cannotBoth: "That connection can't read both ways. Change its words instead.",
  cannotTurn: "That connection can't read the other way round. Change its words instead.",
  reloadFirst: "The connections changed while saving. Close this panel, open it again and try the rest.",
  makeSecret: "Make secret",
  pin: "Pin as true",
  remove: "Remove",
  save: "Save changes",
  discard: "Discard changes",
  saving: (n: number, of: number): string => (of === 1 ? "Saving your change" : `Saving change ${n} of ${of}`),
  saved: "Saved.",
  stagedHint: "Changes here wait until you press Save.",
  // The question before leaving with changes pending, and its two answers.
  leaveAsk: "You have unsaved changes. Leave and lose them?",
  leave: "Leave without saving",
  stay: "Stay",
  // The reason comes first, then what is still there and what to do (the reason is the bot's or the page's own plain line).
  notSaved: (reason: string): string => `Not saved: ${reason} Your changes are still here. Press Save changes to try again.`,
  partlySaved: (done: number, of: number, reason: string): string => `Saved ${done} of ${of}. Not saved: ${reason} The rest are still here. Press Save changes to try again.`,
} as const;
