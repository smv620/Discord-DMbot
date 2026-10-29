// The few words My Account shows about the pages it links to: each card's
// heading, one line and link. They live here, apart from those pages' full word lists, so the
// account page loads only these and not every word of every page it links to; each page's own
// words file takes them from here, so there is one copy.
export const recapCard = {
  linkToList: "Read your session recaps",
} as const;

export const houseRulesCard = {
  heading: "House rules",
  link: "Read your table's house rules",
  lead: "The rules your table plays by. Anyone in the Discord server can read them. Only your DM can change them, on the DM screen in Discord.",
} as const;

export const homebrewCard = {
  heading: "Homebrew",
  link: "Read your table's homebrew",
  lead: "Spells, monsters, items and other game content your DM made for your table. Everyone in your Discord server can read them.",
} as const;

export const transcriptsCard = {
  heading: "Session transcripts",
  link: "Read session transcripts",
  lead: "Read what was said at your table, or save it as a text file. Anyone in the Discord server can read these. Only people who agreed were recorded.",
} as const;
