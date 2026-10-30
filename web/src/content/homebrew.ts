// The words of the homebrew pages (#1525). Everything the DM made for the table is here, nothing
// is hidden from players (#1643); an entry the DM turned off still shows, marked. The DM adds and
// changes entries in Discord, never here.
import { homebrewCard } from "./account-cards";

export const text = {
  heading: homebrewCard.heading,
  link: homebrewCard.link,
  lead: homebrewCard.lead,
  leadOneCampaign:
    "Everything your DM made for this table. An entry marked (off) is kept, but DMbot ignores it.",
  off: "(off)",
  rulesNote: "Looking for rules your table made up?",
  rulesLink: "See House rules",
  dmChanges:
    "You can't change these here. Your DM does that in Discord: ⚙️ Manage DMbot → 📚 Homebrew.",
  loading: "Loading…",
  empty:
    "Nothing shared yet. When your DM shares homebrew with the table, it will show here.",
  signedOut: "Sign in with Discord to read your table's homebrew.",
  signIn: "Sign in with Discord",
  account: "Back to My Account",
  allCampaigns: "All campaigns",
  open: "Read",
  openCampaign: (name: string): string => `Read: ${name}`,
  campaignLine: (server: string, entries: number, packs?: number): string =>
    [
      server,
      `${entries} ${entries === 1 ? "entry" : "entries"}`,
      ...(packs && packs > 0 ? ["follows another table's pack"] : []),
    ].join(" · "),
  packsHeading: "Also following: homebrew from another table",
  packsNote:
    "These come from another table's pack. If one has the same name as one of your table's own above, your table's wins.",
  packLine: (name: string, version: number): string => `${name} (version ${version})`,
  emptyButPack:
    "Your table has no homebrew of its own yet. It follows another table's, shown below.",
  downloadNotePacks: "The file has only your table's own homebrew, not the homebrew from another table.",
  packNone: "That pack has no entries yet.",
  kinds: {
    spell: "Spells",
    monster: "Monsters",
    item: "Items",
    feat: "Feats",
    condition: "Conditions",
    species: "Lineages",
    subclass: "Subclasses",
    background: "Backgrounds",
    other: "Other",
  } as Record<string, string>,
  fromBook: "From a shared book",
  legacy: "[Legacy 2014]",
  bookNote:
    "From a shared book: this came from a rulebook your game master shared with the table. [Legacy 2014]: that book is for the 2014 rules, but your table plays the newer ones.",
  download: "Save as a text file",
  downloadNote: "The file has only what's shown on this page.",
  downloading: "Saving…",
  downloadFailed: "The file didn't save. Try again.",
  notFound:
    "We can't find that campaign's homebrew. It may have been removed. Go to All campaigns and pick again.",
  failed: "That didn't load. Try again in a moment.",
  tooMany: "Lots of people are asking at once. Wait a moment, then try again.",
};

/** The kinds in the order the bot lists them. */
export const kindOrder = [
  "spell",
  "monster",
  "item",
  "feat",
  "condition",
  "species",
  "subclass",
  "background",
  "other",
];

export function problem(kind: string): string {
  if (kind === "not-found") return text.notFound;
  if (kind === "slow-down") return text.tooMany;
  return text.failed;
}
