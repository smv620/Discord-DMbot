// The words of the homebrew pages (#1525). Only what the DM let the players know is here: the DM
// keeps the rest behind the DM screen. The DM adds and changes entries in Discord, never here.
export const text = {
  heading: "Homebrew",
  link: "Read your table's homebrew",
  lead: "Spells, monsters, items and rules your DM made for your table. Everyone in your Discord server can read them.",
  leadOneCampaign:
    "Your DM chose to share these. Your DM may have more that stays behind the DM screen.",
  dmChanges:
    "You can't change these here. Your DM does that in Discord: ⚙️ Settings → 📚 Homebrew.",
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
    "These come from another table's pack. It shows only what that table lets players know. If one has the same name as one of your table's own above, your table's wins.",
  packLine: (name: string, version: number): string => `${name} (version ${version})`,
  emptyButPack:
    "Your table has no homebrew of its own yet. It follows another table's, shown below.",
  downloadNotePacks: "The file has only your table's own homebrew, not the homebrew from another table.",
  packNone: "That table hasn't shared any of it with players yet.",
  kinds: {
    spell: "Spells",
    monster: "Monsters",
    item: "Items",
    feat: "Feats",
    condition: "Conditions",
    rule: "Rules",
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
  "rule",
  "other",
];

export function problem(kind: string): string {
  if (kind === "not-found") return text.notFound;
  if (kind === "slow-down") return text.tooMany;
  return text.failed;
}
