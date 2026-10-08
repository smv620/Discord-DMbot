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
  feedback: {
    heading: "Feedback",
    label: "What do you think of DMbot?",
    send: "Send feedback",
    contactLabel: "How can we reach you?",
    contactHint: "You can skip this. A Discord name or an email. Only the team sees this.",
  },
  question: {
    heading: "Ask a question",
    label: "What would you like to know?",
    send: "Send question",
    contactLabel: "How should we answer you?",
    contactHint:
      "A Discord name or an email. Only the team sees this. Skip it and we can't reply to you.",
  },
  messageHint: "Everyone can read your message, so leave out private details.",
  sending: "Sending…",
  sent: "Thanks, we read every message.",
  sendAnother: "Send another",
  errors: {
    empty: "Please type your message first.",
    "too-long": `That's too long. Please keep it to ${MAX_MESSAGE.toLocaleString("en")} letters or fewer.`,
    "slow-down":
      "You sent a message a moment ago. Wait 10 minutes, then press send again. Your words are still here.",
    "not-human":
      "We couldn't check that you're a person. Wait for the tick just above the button, then send again. No tick? Reload the page, or try another browser.",
    "contact-too-long": `How to reach you is too long. Please keep it to ${MAX_CONTACT} letters or fewer.`,
    failed: "That didn't send. Your words are still here. Please try again later.",
  },
  needsScript:
    "These forms need JavaScript. Turn it on, then reload the page. Developers can still open an issue on GitHub below.",
  developers: "Developers: suggestions and bug reports are welcome as GitHub issues.",
  developersLink: "Open an issue on GitHub",
} as const;

/** "1,950 letters left". */
export function lettersLeft(length: number): string {
  const left = MAX_MESSAGE - length;
  if (left < 0) return `${(-left).toLocaleString("en")} letters too many`;
  return `${left.toLocaleString("en")} ${left === 1 ? "letter" : "letters"} left`;
}
