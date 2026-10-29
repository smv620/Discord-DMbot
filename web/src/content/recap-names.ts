// The recap page's "Names in this campaign" part (#1451): the DM tells the narrator how to say a
// name ("Kah-zeth" for Ka'zeth). Plain words. It shows only while recaps AND narration are on
// (recap-words.ts): a spelling does nothing without a narrator, so the page must not offer one.
// Secret names are never listed, and the words say so, because it is true: the API's database
// door leaves them out and the narrator is never sent one.
export const nameWords = {
  heading: "Names in this campaign",
  lead: "The narrator reads your recap aloud. If it says a name wrong, type it the way it sounds. For Ka'zeth, type Kah-zeth. Leave the box empty and the narrator says the name as written.",
  secret: "Secret names are not listed here. The narrator never sees them.",
  loading: "Loading the names…",
  none: "No names yet. Names show up here once you have confirmed them in DMbot.",
  label: (name: string) => `How to say ${name}`,
  save: "Save",
  saving: "Saving…",
  saved: "Saved.",
  cleared: "Cleared. The narrator will say it as written.",
  tooLong: "That is too long. Use 80 letters or fewer.",
  odd: "Use only letters, numbers and ordinary signs, then press Save again.",
  failed: "That didn't save. Check your connection, then press Save again.",
  signedOut: "You were signed out. Sign in again, then press Save.",
  gone: "That name is no longer in this campaign. Reload the page.",
  more: "Only the first 200 names are shown.",
  loadFailed: "The names didn't load. Reload the page to try again.",
} as const;

/** The most letters a spelling may have (the same limit as the bot's name card). */
export const SAID_AS_MAX = 80;
