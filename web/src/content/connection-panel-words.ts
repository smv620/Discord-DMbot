// The words of the redesigned connection panel (#4155). Names are only ever text on the page. Plain words: the DM decides, the
// panel only stages what they pick until they press Save.
export const panelWords = {
  heading: (a: string, b: string): string => `${a} and ${b}`,
  add: "+ Add a connection",
  addClose: "Close the form",
  none: "No connections between these two yet.",
  listLabel: "Connections between these two",
  unsaved: "Unsaved changes",
  unsavedShort: "Unsaved",
  // The arrow reads with the names in the order the panel shows them (a first).
  arrow: (a: string, b: string, direction: "a-b" | "b-a" | "both"): string =>
    direction === "a-b" ? `${a} → ${b}` : direction === "b-a" ? `${a} ← ${b}` : `${a} ↔ ${b}`,
  open: (line: string): string => `Open: ${line}`,
  close: "Close",
  words: "Words",
  makeSecret: "Make secret",
  makeKnown: "Make known",
  pin: "Pin as true",
  unpin: "Unpin",
  remove: "Remove",
  save: "Save changes",
  discard: "Discard changes",
  saving: "Saving your changes",
  saved: "Saved.",
  stagedHint: "Changes here wait until you press Save.",
  // The question before leaving with changes pending, and its two answers.
  leaveAsk: "You have unsaved changes. Leave and lose them?",
  leave: "Leave",
  stay: "Stay",
  partlySaved: (done: number, of: number): string => `Saved ${done} of ${of}. The rest are still waiting; press Save to try again.`,
} as const;
