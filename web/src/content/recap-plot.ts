// The words for the "Plot check" on a recap's page (#2124; core #2122, #2123): PlotBot reads the recap
// against the campaign's earlier sessions and facts and raises questions. DMbot advises and the DM
// decides, so every question has an answer that leaves the recap exactly as it is. Shown only while
// PLOT_CHECK_LIVE is true (recap-words.ts), to the campaign's DM, never on the public recap link.
export const PLOT_NOTE_MAX_LETTERS = 200;
export const PLOT_CHAT_MAX_LETTERS = 300;

export type PlotAnswerWord = "fix" | "changed" | "fine" | "leave";

export const plotWords = {
  heading: "Plot check",
  lead: "DMbot compared this recap with your earlier sessions and notes. These are questions, not rulings. You decide every one.",
  checking: "Checking the recap…",
  checkingChange: "Checking the change…",
  slow: "The check is taking a while. Open this page again in a minute. You can still read and edit the recap.",
  none: "Nothing to ask. This recap fits what DMbot knows.",
  loadFailed: "We couldn't load the plot check. Nothing was changed. Open this page again in a minute.",
  level: { high: "Worth a look", medium: "Maybe", low: "Small" } as Record<string, string>,
  kind: {
    contradiction: "Doesn't match earlier",
    dead_acts: "Someone who was gone",
    item_twice: "An item in two places",
    place: "A place that doesn't fit",
    timeline: "Order of events",
    dropped_thread: "A story thread dropped",
    promise: "A promise not kept",
    secret: "A secret out early",
  } as Record<string, string>,
  recapSays: "The recap says:",
  earlier: (session: number | null): string => (session === null ? "Earlier:" : `Earlier, session ${session}:`),
  answers: {
    fix: "Fix the recap",
    talk: "Talk it through",
    fine: "Not a problem",
    leave: "Leave it as is",
  },
  // (the spoken name starts with the words on the button, so voice control finds it)
  answerFor: (label: string, n: number): string => `${label}, question ${n}`,
  fixPrefill: (reason: string, sentence: string): string => `${reason} (“${sentence}”)`,
  fixed: "Added to the box under “Tell DMbot what to fix”. Send it there when you are ready.",
  yourAnswer: {
    fix: "You chose: fix the recap.",
    changed: "You chose: the story changed.",
    fine: "You chose: not a problem.",
    leave: "You chose: leave it as is.",
  } as Record<string, string>,
  change: "Change my answer",
  noteLabel: "Why it's fine (a few words, if you like)",
  noteSave: "Save: not a problem",
  noteLeft: (n: number): string => `${n} letters left`,
  saved: "Saved.",
  chatHeading: "Talk it through",
  chatLead: "Say what happened. DMbot may suggest a note to keep. Nothing is saved unless you press Save.",
  chatLabel: "Your message",
  chatSend: "Send",
  chatSending: "Sending…",
  chatBot: "PlotBot",
  chatYou: "You",
  proposal: "Suggested note:",
  save: "Save",
  notThat: "Not that",
  proposalSaved: "Saved as your note.",
  proposalDeclined: "Not saved.",
  chatClose: "Close the chat",
  versions: "Approved versions",
  versionLine: (version: number, who: string, when: string): string => `Version ${version}, approved by ${who} on ${when}`,
  noVersions: "No version has been approved yet.",
  waitingOn: (n: number): string =>
    n === 1 ? "Answer the question in the Plot check before you approve." : `Answer the ${n} questions in the Plot check before you approve.`,
  waitingCheck: "The Plot check is still running. You can approve when it is done.",
  problem: {
    "not-allowed": "Only the game master can answer this.",
    "too-soon": "That's a lot at once. Wait a moment and try again.",
    "not-found": "That question is already gone. The list is up to date now.",
    "bad-text": "That can't be sent. Use a few plain words.",
    network: "We couldn't reach DMbot. Nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};
