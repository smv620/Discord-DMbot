// The words of the relationship web page (#2324; docs/PLAN.md, "The relationship web on the website"):
// the names of a campaign as dots, the connections between them as lines. Plain words. DMbot never
// decides: a DM changes what DMbot remembers, and Undo is on the name's card in Discord.
/** Over this many names the page asks before drawing (PLAN: "about 300"). */
export const BIG_WEB = 300;

export const webPage = {
  heading: "Relationship web",
  lead: "How the names in this campaign are connected. Pick a name for the middle and how far out to look. Tap a name to put it in the middle, or a line to see its connections.",
  loading: "Loading…",
  signedOut: "You were signed out. Sign in again.",
  notYours: "We can't show that web. Players can read about NPCs from the NPC list in Discord.",
  down: "We couldn't reach DMbot. Check your connection and try again.",
  tryAgain: "Try again",
  empty: "No names are connected here yet.",
  centreLabel: "Middle of the web",
  centreSearch: "Find a name",
  centreHint: "Type part of a name, then choose it.",
  centreNone: "No name matches.",
  centreNow: (name: string): string => `In the middle: ${name}`,
  hopsLabel: "How far out",
  hopsHint: (max: number): string => `1 is just the names it is connected to. Up to ${max}.`,
  bigAsk: (n: number): string => `This will draw ${n} names and may be slow. Show it anyway?`,
  bigYes: "Show it anyway",
  bigNo: "Not now",
  truncated: "There are more names than fit on the map. Choose a smaller number to see them all.",
  howMany: (names: number, lines: number): string =>
    `${names} ${names === 1 ? "name" : "names"}, ${lines} ${lines === 1 ? "line" : "lines"}`,
  mapLabel: (names: number, centre: string): string =>
    `A map of ${names} ${names === 1 ? "name" : "names"} around ${centre}. The same names and connections are listed below it.`,
  zoomIn: "Zoom in",
  zoomOut: "Zoom out",
  resetView: "Reset the view",
  panHint: "Drag to move around. Pinch, or use the buttons, to zoom.",
  legendHeading: "What the shapes mean",
  // Kinds the API may send; any other key is shown as it comes.
  kinds: {
    npc: "People",
    place: "Places",
    group: "Groups",
    item: "Items",
    event: "Events",
  } as Record<string, string>,
  secretMark: "🤫",
  secretWords: "Secret: only the DM sees this",
  listHeading: "The same web, as a list",
  namesHeading: "Names",
  linesHeading: "Connections",
  makeMiddleShort: "Middle",
  seeLineShort: "See",
  save: "Save",
  makeMiddle: (name: string): string => `Put ${name} in the middle`,
  inMiddle: "In the middle",
  seeLine: (a: string, b: string): string => `See the connections between ${a} and ${b}`,
  lineText: (a: string, b: string, label: string, more: number): string =>
    `${a} ↔ ${b}: ${label}${more > 0 ? ` (+${more})` : ""}`,
  readOnly: "Only this campaign's DM can change names and connections.",
  undoNote: "A change made here can be undone with ↩️ Undo on the name's card in Discord.",
  close: "Close",
  nodePanel: (name: string): string => `About ${name}`,
  kindIs: (kind: string): string => `It is: ${kind}`,
  spellLabel: "Spelling",
  saveSpelling: "Save spelling",
  kindChange: "What it is",
  saveKind: "Save what it is",
  edgePanel: (a: string, b: string): string => `${a} and ${b}`,
  linkText: (a: string, b: string, words: string, direction: "ab" | "ba" | "both"): string =>
    direction === "ab" ? `${a} ${words} ${b}` : direction === "ba" ? `${b} ${words} ${a}` : `${a} and ${b}: ${words}`,
  wordsLabel: (n: number): string => `Words for connection ${n}`,
  saveWords: (n: number): string => `Save words for connection ${n}`,
  makeSecret: (n: number): string => `Make connection ${n} secret`,
  makeKnown: (n: number): string => `Make connection ${n} known to the players`,
  removeLink: (n: number): string => `Remove connection ${n}`,
  removeAsk: "Remove this connection? DMbot forgets it.",
  removeYes: "Yes, remove it",
  keep: "Keep it",
  addHeading: "Add a connection",
  addWords: "Words, like “works with”",
  addButton: "Add",
  addSecret: "Secret: only the DM sees this",
  saved: "Saved.",
  added: "Added.",
  removed: "Removed.",
  errors: {
    "bad-name": "Type some words for the connection, like “works with”.",
    "not-allowed": "Only this campaign's DM can change names and connections.",
    "not-found": "That is gone. The web is up to date now.",
    "needs-confirm": "Press the Yes button to do that.",
    network: "We couldn't reach DMbot, so nothing was changed. Check your connection and try again.",
  } as Record<string, string>,
  errorFallback: "That didn't work. Try again in a minute.",
} as const;

/** The words for a kind: the API's key if no words are known. */
export function kindWords(key: string): string {
  return webPage.kinds[key] ?? key;
}
