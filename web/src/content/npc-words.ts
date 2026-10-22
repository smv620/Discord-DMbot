// The words of an NPC's page (#2266; docs/PLAN.md, "Every NPC opens its own page, with private
// notes"). Plain words. The page shows what the players already see in Discord's NPC list, never a
// secret; My notes belong to one person, and nobody else (not the DM) can read them.
/** The longest a note may be: core's `NOTE_MAX` (web/npc_pages.py). */
export const NOTE_LIMIT = 5000;

/** What a kind is, in plain words. A kind this table doesn't know shows as it came. */
const kindWords: Record<string, string> = {
  npc: "A person",
  player_character: "A player's character",
  place: "A place",
  group: "A group",
  item: "An item",
  event: "An event",
  creature: "A creature",
};

export const npcPage = {
  heading: "NPC",
  loading: "Loading…",
  signedOut: "You were signed out. Sign in again.",
  notInServer: "This page is for the people in that Discord server. Join the server, then sign in again.",
  notFound: "We couldn't find that name. Go back to Discord and press 📖 Open again.",
  down: "We couldn't reach DMbot. Check your connection and try again.",
  tryAgain: "Try again",
  kindOf: (kind: string): string => kindWords[kind] ?? kind,
  alsoCalled: "Also called",
  groups: "Groups",
  ties: "Close ties",
  more: (n: number): string => (n === 1 ? "and 1 more" : `and ${n} more`),
  recaps: "Mentioned in these recaps",
  recapLink: (title: string, day: string): string => `${title} (${day})`,
  nothingElse: "That is all the table knows so far.",
  dmNote: "DM note",
  dmNoteWho: "Only the DM sees this. It is the same note as on this name's card in Discord.",
  notesHeading: "My notes",
  notesWho: "My notes: only you can see these.",
  notesLabel: "Your notes on this name",
  counter: (n: number): string => `${n} of ${NOTE_LIMIT} letters`,
  save: "Save",
  saving: "Saving…",
  saved: "Saved.",
  saveFailed: "That didn't save. Check your connection and try Save again.",
  tooLong: `That note is too long. The most it can hold is ${NOTE_LIMIT} letters.`,
  tooSoon: "Please wait a moment, then press Save again.",
  allHeading: "Notes on all names in this campaign",
  download: "Download my notes",
  downloading: "Getting your notes…",
  downloadFailed: "We couldn't get your notes. Try again.",
  noneYet: "You have no notes in this campaign yet.",
  deleteAsk: "Delete my notes",
  deleteSure: (n: number): string =>
    n === 1
      ? "Delete your 1 note in this campaign? This can't be undone."
      : `Delete your ${n} notes in this campaign? This can't be undone.`,
  deleteYes: "Yes, delete my notes",
  deleteNo: "Keep them",
  deleted: (n: number): string => (n === 1 ? "Deleted 1 note." : `Deleted ${n} notes.`),
  deleteFailed: "We couldn't delete your notes. Try again.",
  unlistedHeading: "Notes on names no longer listed",
  unlistedNote:
    "These names were taken off the NPC list. DMbot keeps your notes on them for 30 days, then deletes them. You can download or delete them now.",
  fileHeading: "My notes",
  fileName: "my-npc-notes.txt",
};
