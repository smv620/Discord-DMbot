// Links into the Help page (#1946). People paste "/help#some-section" into Discord, so an address
// that worked once must keep working. `knownAnchors` is every address the page had before the
// section and step links were added; a test fails if one of them goes. When a heading is renamed,
// add its old address to `anchorAliases` (old address -> the address now) and the page keeps
// answering to both.

/** Every id the Help page had before #1946, with whether it is always on the page. */
export const knownAnchors: { id: string; always: boolean }[] = [
  { id: "top", always: true },
  { id: "main", always: true },
  { id: "contents-heading", always: true },
  { id: "dm-heading", always: true },
  { id: "quick-heading", always: true },
  { id: "recaps-heading", always: false }, // only while recaps are on
  { id: "player-heading", always: true },
  { id: "account-heading", always: true },
  { id: "emails-heading", always: true },
  { id: "snags", always: true },
  { id: "snags-heading", always: true },
  { id: "getting-help", always: true },
  { id: "getting-help-heading", always: true },
];

/** Old address -> the address that replaced it. Empty until a heading is renamed. */
export const anchorAliases: Record<string, string> = {};

/** The old addresses that now point at this one. */
export const aliasesFor = (id: string): string[] =>
  Object.entries(anchorAliases)
    .filter(([, now]) => now === id)
    .map(([old]) => old);

/** The ids of the page's sections, from the words of their headings. Two keep the id they always had. */
export const sectionIds = {
  gameMaster: "game-master",
  players: "players",
  account: "account",
  emails: "emails",
  snags: "snags",
  gettingHelp: "getting-help",
} as const;

/**
 * Hands out "game-master-1", "game-master-2", … in the order the steps are drawn. A step's address is
 * its place, so a link keeps pointing at the same step only while nothing is put in front of it:
 * add new steps at the end of a list. (A test pins a few addresses to their words so a shift shows.)
 */
export const stepCounter = (section: string): (() => string) => {
  let n = 0;
  return () => `${section}-${++n}`;
};
