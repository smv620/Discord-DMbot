// The Story graph's list of what waits for the DM (#2635, #2652): facts PlotBot heard at the table,
// connections found in a backstory (grouped by the upload each came from) and "Which Mara?" questions.
// Plain words; the DM decides everything here, DMbot only asks. Nothing is shown to players: the API
// answers only a DM of the campaign. WEB_WAITING_LIVE moves with the What's new entry "What is waiting
// for you, on the website"; it needs CloudDev's #2638 and #2654 deployed and dev1 to have seen one fact
// confirmed and one "Which Mara?" answered from the site.
export const WEB_WAITING_LIVE = false;

export const waitingWords = {
  heading: "Waiting for you",
  count: (n: number): string => (n === 1 ? "1 thing is waiting for you." : `${n} things are waiting for you.`),
  none: "Nothing is waiting for you.",
  intro: "DMbot never decides these. Each one needs your answer before it goes into your story.",
  more: "The lists below show the first ones. Answer some and the rest will follow.",

  claimsHeading: "Said at the table",
  claimsNote: "PlotBot heard these. Say whether each one is true.",
  confirm: "Yes, that's true",
  confirmSecret: "True, but keep it secret",
  reject: "Not right",

  backstoryHeading: "From your backstory",
  backstoryNote: "Connections found in the backstory you uploaded.",
  fromUpload: (label: string): string => `From ${label}`,
  fromEarlier: "From an earlier upload",
  keep: "Keep",
  keepSecret: "Keep as secret",
  skip: "Skip",
  keepAll: (label: string, n: number): string => `Keep all from ${label} (${n})`,
  keepAllEarlier: (n: number): string => `Keep all from an earlier upload (${n})`,
  keepAllCut: (done: number, left: number): string =>
    `Kept ${done}. ${left} more are ready: press the button again to keep them.`,
  waitingOn: (names: string[]): string =>
    `Answer ${names.join(" and ")} in Check new names first, then this one can be kept.`,
  secretNote: "Players won't see this one.",

  whichHeading: "Which one?",
  whichNote: "A name in your backstory matches more than one entry.",
  whichQuestion: (word: string): string => `Which ${word} is this?`,
  whichLine: (other: string, how: string, word: string, otherIsSubject: boolean): string =>
    otherIsSubject ? `${other} ${how} ${word}` : `${word} ${how} ${other}`,
  neither: "Neither",
  hiddenFromPlayers: "(hidden from players)",

  working: "Sending your answer to DMbot…",
  workingStage: "DMbot is applying your answer",
  done: {
    confirm: "Added to your story as true.",
    confirmSecret: "Added to your story as true, and kept secret.",
    reject: "Dropped. DMbot won't ask about it again.",
    keep: "Kept.",
    keepSecret: "Kept as a secret.",
    skip: "Skipped.",
    pick: "Done. That connection is ready to keep.",
    neither: "Dropped.",
  },
  kept: (n: number): string => (n === 1 ? "Kept 1 connection." : `Kept ${n} connections.`),

  errors: {
    gone: "That one isn't waiting any more. The list now shows what is.",
    "name-first": "Answer the new name in Check new names first, then try this again.",
    "stopped-recording": "Someone who said it has stopped being recorded, so DMbot threw it away.",
    "cannot-write": "DMbot can't write that now, because the rules refuse it. It stays waiting.",
    "queue-full": "Too many connections are waiting. Answer some of them first, then try again.",
    "still-waiting": "DMbot hasn't finished yet. Look again in a moment; it will not be done twice.",
    "not-found": "That one isn't there any more. The list now shows what is.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    other: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string>,
  loadFailed: "We couldn't read what is waiting. Try again in a minute.",
  reload: "Look again",
};
