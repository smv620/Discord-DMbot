/**
 * Every word on the "Say hello" page (#665). Plain words; every message says what to do
 * next. The privacy line and the two thank-you and try-later messages are the ones the
 * issue asked for.
 */
export const MAX_MESSAGE = 2000;
export const MAX_CONTACT = 200;
export const REPO_URL = "https://github.com/smv620/Discord-DMbot";

export const text = {
  title: "Say hello",
  description: "Tell the DMbot team what you think, or ask us a question.",
  heading: "Say hello",
  intro: "Tell us what you think of DMbot, or ask us anything.",
  qaNote: "Quick answers may already be in the",
  qaLink: "Q&A",
  privacy: "Only your message is shown in public. How to reach you stays with the team.",
  publicPost:
    "We post your message on GitHub, a public website anyone can read and search. Leave out names, emails and anything private.",
  feedback: {
    heading: "Feedback",
    label: "What do you think of DMbot?",
    send: "Send feedback",
    contactLabel: "How can we reach you?",
    contactHint: "Optional. A Discord name or an email. Only the team sees this.",
  },
  question: {
    heading: "Ask a question",
    label: "What would you like to know?",
    send: "Send question",
    contactLabel: "How should we answer you?",
    contactHint:
      "Optional. A Discord name or an email, so we can answer you. Skip it, and we'll answer on GitHub instead. Only the team sees this.",
  },
  messageHint: "Anyone can read this. Put how to reach you in the next box, not here.",
  sending: "Sending…",
  sent: "Thanks, we read every message.",
  seePost: "See your message on GitHub",
  answerOnGitHub: "We'll answer under your message on GitHub.",
  sendAnother: "Send another",
  errors: {
    empty: "Please type your message first.",
    "too-long": `That's too long. Please keep it to ${MAX_MESSAGE.toLocaleString("en")} letters or fewer.`,
    "slow-down":
      "We take one message every 10 minutes from each connection. Wait a few minutes, then press send again. Your words are still here.",
    busy: "Lots of people are writing right now. Try again in a few minutes. Your words are still here.",
    "not-human":
      "Wait for the tick above, then press send again. Your words are still here.",
    off: "Messages are switched off for now. Please try again later, or open an issue on GitHub (the link is at the bottom of this page).",
    "contact-too-long": `How to reach you is too long. Please keep it to ${MAX_CONTACT} letters or fewer.`,
    failed: "That didn't send. Your words are still here. Please try again later.",
  },
  checkFailed:
    "The person check didn't load. Try another browser, or try again later. Your words are still here.",
  needsScript:
    "These forms need JavaScript. Turn it on, then reload the page. Developers can still open an issue on GitHub below.",
  developers: "Developers: suggestions and bug reports are welcome as GitHub issues.",
  developersLink: "Open an issue on GitHub",
  repoLink: "See the code on GitHub",
} as const;

/** Shown once this few letters are left, so the count isn't noise while typing. */
export const COUNT_FROM = 200;

/** "150 letters left". */
export function lettersLeft(length: number): string {
  const left = MAX_MESSAGE - length;
  if (left < 0) return `${(-left).toLocaleString("en")} letters too many`;
  return `${left.toLocaleString("en")} ${left === 1 ? "letter" : "letters"} left`;
}
