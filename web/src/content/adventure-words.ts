// The words of the Shared story adventure on the website (#3694, slices A and B). Discord's own (dm_screen/adventure_card.py), so the
// two places read the same; the page only adds what the card says with buttons. The book's text is the DM's alone: nothing here
// is a chapter, a place or a name from any book.
export const adventureWords = {
  heading: "Your adventure",
  intro: "DMbot keeps the text of your files, not the files, for this campaign only. Players never see it. None of this is in the campaign's backup: if you restore a backup, give the files again.",
  empty: "Nothing yet. For now, give a file in Discord.",
  notOn: "Not yet. In Settings, turn on Shared story and say you have the right to use the material.",
  loading: "Looking at your adventure…",
  down: "Couldn't look at your adventure just now. Try again in a minute.",
  tryAgain: "Try again",
  list: "Files",
  pages: (characters: number): string => {
    const n = Math.max(1, Math.round(characters / 2000));
    return `about ${n} ${n === 1 ? "page" : "pages"}`;
  },
  giveAgain: "give DMbot this file again to use it",
  notRead: "not read yet",
  readOf: (found: string, skipped: number): string => `read: ${found}${skipped > 0 ? `, ${skipped} ${skipped === 1 ? "part" : "parts"} couldn't be read` : ""}`,
  reading: (percent: number): string => `reading… ${percent}%`,
  stopped: (percent: number, why: string): string => `stopped at ${percent}%. ${why}`,
  restarted: "because DMbot restarted. Press Read it now in Discord to carry on.",
  carryOn: "Press Read it now in Discord to carry on.",
  count: (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`,
  chapter: ["chapter", "chapters"],
  place: ["place", "places"],
  person: ["person", "people"],
  link: ['"this needs that" note', '"this needs that" notes'],
  heldMore: (held: number, max: number): string => `${held.toLocaleString("en-US")} of ${max.toLocaleString("en-US")} characters kept.`,
  see: "See what was read",
  hide: "Hide what was read",
  seeing: "Looking at what was read…",
  nothingRead: "Nothing was read yet. Press Read it now in Discord first.",
  more: "Show more",
  of: (shown: number, total: number): string => `Showing ${shown} of ${total}`,
  kinds: { chapter: "Chapter", place: "Place", person: "Person", link: "This needs that" } as Record<string, string>,
  // Why a read stopped, by core's code (Discord's STOPPED_WORDS).
  why: {
    file_cap: "This file is longer than DMbot can read in full. What it read is under See what was read.",
    month_cap: "DMbot has used up its reading for this month. It can carry on next month.",
    unreachable: "DMbot's AI could not be reached. Press Read it now in Discord to try again.",
    funds: "DMbot can't read right now. Try again later.",
    time: "It took too long. Press Read it now in Discord to try again.",
    off: "Shared story was turned off. Turn it on again, then press Read it now in Discord.",
    plan: "This campaign's plan doesn't include reading adventures.",
    error: "Something went wrong. Press Read it now in Discord to try again.",
    price: "DMbot stopped because the AI's price went up. Press Read it now in Discord later to carry on.",
    gone: "That file was removed while DMbot was reading it. Give it again, then press Read it now in Discord.",
  } as Record<string, string>,
} as const;
