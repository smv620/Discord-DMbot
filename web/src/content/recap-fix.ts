// The words for "Tell DMbot what to fix" on the recap page (#2114; core #2113): the DM types a note,
// DMbot sorts it into points (corrections, style notes, and plain answers), the DM keeps the ones
// they want, and the recap is written again with those. The DM decides; DMbot only sorts and
// rewrites. Shown only while RECAP_FIX_LIVE is true (recap-words.ts).
export const FIX_MAX_LETTERS = 1000;

export const fixWords = {
  heading: "Tell DMbot what to fix",
  lead: "Type what to change. DMbot sorts your note into points and you choose which to keep. Nothing changes until you press Rewrite with these, and DMbot only changes what you ask. It never adds story.",
  label: "What should change?",
  count: (n: number): string => `${n} of ${FIX_MAX_LETTERS} letters`,
  send: "Send",
  sending: "Sending…",
  sorting: "Sorting your note…",
  slow: "This is taking longer than usual. Your note is still being sorted. Press Check again.",
  checkAgain: "Check again",
  sorted: (n: number): string => `Done. ${n} ${n === 1 ? "point" : "points"} to look at.`,
  nothing: "DMbot found nothing to change. Say it another way and press Send.",
  correctionsHeading: "Changes to make",
  styleHeading: "Style notes",
  styleLead:
    "Style notes you keep are saved for this campaign's later recaps too. The newest 10 are kept. You can remove them in ⚙️ Manage DMbot → 📜 Recaps → ✍️ Recap style. These start not kept: press Keep on the ones you want.",
  repliesHeading: "Answer from DMbot (nothing is changed by this)",
  remove: "Remove",
  removeOne: (text: string): string => `Remove: ${text}`,
  putBack: "Put back",
  putBackOne: (text: string): string => `Put back: ${text}`,
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
    `This uses one Try again (${triesLeft} left), and you approve the new text again.`,
  costNoCount: "This uses one Try again, and you approve the new text again.",
  noTriesLeft: "This recap has no Try again left, so it can't be written again. You can still edit the words yourself.",
  started: "DMbot is writing the recap again. This page shows it when it is ready, and you approve the new text again.",
  startedNoApproval: "DMbot is writing the recap again. This page shows it when it is ready.",
  problem: {
    "not-allowed":
      "Recaps are turned off for this campaign or its plan right now. Check ⚙️ Manage DMbot → 📜 Recaps.",
    "too-big": `That note is too long. Keep it under ${FIX_MAX_LETTERS.toLocaleString("en-US")} letters.`,
    "too-soon": "You sent a note a moment ago. Wait a minute, then press Send.",
    "too-soon-rewrite": "Wait a minute, then press Rewrite with these again.",
    "not-editable":
      "This recap can't change now (it was posted or skipped, or it is being written). Open the recap list to see where it is.",
    "bad-text": "Type what you'd like changed first.",
    "no-tries": "This recap has no Try again left, so it can't be written again. You can still edit the words yourself.",
    "sort-failed": "DMbot couldn't sort your note. Try again.",
    "bad-choice": "Those points are out of date. Press Send again to get fresh ones.",
    "not-found": "We can't find that recap. Reload the page.",
    "signed-out": "You were signed out. Sign in again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string> & { server: string },
} as const;
