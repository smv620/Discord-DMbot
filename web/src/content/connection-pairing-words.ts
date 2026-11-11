// The words for joining two names on the map (#4155, part 3): picking the first name, then the other.
export const pairingWords = {
  /** The button in a name's panel (for a phone, which has no Ctrl key). */
  connectTo: "Connect to another name",
  /** Shown while one name is picked and the other is awaited. */
  pickSecond: (name: string): string => `Joining ${name || "this name"}. Tap the other name.`,
  cancel: "Cancel",
  /** One quiet line for people with a keyboard; the button above does the same on a phone. */
  ctrlHint: "On a computer you can also Ctrl+click (Mac: ⌘+click) one name, then tap the other.",
  /** Beside the connections in a name's panel. */
};
