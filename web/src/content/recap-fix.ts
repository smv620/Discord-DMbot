// The words for "Tell DMbot what to fix" on the recap page (#2114; core #2113): the DM types a note,
// DMbot sorts it into points (corrections, style notes, and plain answers), the DM keeps the ones
// they want, and the recap is written again with those. The DM decides; DMbot only sorts and
// rewrites. Shown only while RECAP_FIX_LIVE is true (recap-words.ts).
export const FIX_MAX_LETTERS = 1000;

export const fixWords = {
  heading: "Tell DMbot what to fix",
  lead: "Type what you'd like changed in this recap. DMbot sorts your note into points, and you choose which ones to keep. Nothing changes until you press Rewrite.",
  label: "What should change?",
  count: (n: number): string => `${n} of ${FIX_MAX_LETTERS} letters`,
  send: "Send",
  sorting: "Sorting your note…",
  slow: "This is taking longer than usual. Press Send to try again.",
  correctionsHeading: "Changes to make",
  styleHeading: "Style notes",
  styleLead: "A style note shapes how recaps are written. Keep the ones you want.",
  repliesHeading: "DMbot says",
  remove: "Remove",
  removeOne: (text: string): string => `Remove: ${text}`,
  keep: "Keep",
  dontKeep: "Don't keep",
  keepOne: (text: string): string => `Keep: ${text}`,
  dontKeepOne: (text: string): string => `Don't keep: ${text}`,
  kept: "Kept",
  notKept: "Not kept",
  rewrite: "Rewrite with these",
  rewriting: "Rewriting…",
  nothingKept: "Keep at least one point to rewrite.",
  cost: (triesLeft: number): string =>
    `This uses one of this recap's tries (${triesLeft} left), and the new text needs approving again.`,
  costNoCount: "This uses one of this recap's tries, and the new text needs approving again.",
  started: "DMbot is writing the recap again. This page shows it when it is ready, and you will need to approve the text again.",
  problem: {
    "not-allowed": "Only a game master of this campaign can change a recap.",
    "too-big": `That note is too long. Keep it under ${FIX_MAX_LETTERS.toLocaleString("en-US")} letters.`,
    "too-soon": "Wait a minute, then try again.",
    "not-editable": "This recap was posted or skipped, so it can't change.",
    "bad-text": "Type what you'd like changed first.",
    "no-tries": "This recap has no tries left, so it can't be written again.",
    "sort-failed": "DMbot couldn't sort your note. Try again.",
    "bad-choice": "One of those points is out of date. Send your note again.",
    "not-found": "We can't find that recap. Reload the page.",
    "signed-out": "You were signed out. Sign in again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string> & { server: string },
} as const;
