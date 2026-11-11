// The words for joining two names on the map (#4155, part 3): picking the first name, then the second.
export const pairingWords = {
  /** The button in a name's panel (for a phone, which has no Ctrl key). */
  connectTo: "Connect to…",
  /** Shown while one name is picked and the second is awaited. */
  pickSecond: (name: string): string => `Now tap the second name to connect it to ${name}.`,
  cancel: "Cancel",
  /** Beside each connection in a name's panel: opens that connection's panel with it already open. */
  open: "Open",
  openLabel: (text: string): string => `Open this connection: ${text}`,
};
