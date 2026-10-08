/**
 * Every word on the "Say hello" page (#665). Plain words; every message says what to do
 * next. The privacy line and the two thank-you and try-later messages are the ones the
 * issue asked for.
 */
export const MAX_MESSAGE = 2000;
export const REPO_URL = "https://github.com/smv620/Discord-DMbot";

export const text = {
  title: "Say hello",
  description: "Tell the DMbot team what you think, or ask us a question.",
  heading: "Say hello",
  intro: "Tell us what you think of DMbot, or ask us anything.",
  privacy: "Only your message is shown in public. How to reach you stays with the team.",
  feedback: {
    heading: "Feedback",
    label: "What do you think of DMbot?",
    send: "Send feedback",
  },
  question: {
    heading: "Ask a question",
    label: "What would you like to know?",
    send: "Send question",
  },
  contactLabel: "How can we reach you? (you can skip this)",
  contactHint: "A Discord name or an email.",
  sending: "Sending…",
  sent: "Thanks, we read every message.",
  sendAnother: "Send another",
  errors: {
    empty: "Please type your message first.",
    "too-long": `That's too long. Please keep it to ${MAX_MESSAGE.toLocaleString("en")} letters or fewer.`,
    "slow-down": "You just sent one. Please wait 10 minutes, then send again.",
    "not-human": "We couldn't check that you're a person. Wait for the tick, then send again.",
    failed: "That didn't send. Please try again later.",
  },
  needsScript: "These forms need JavaScript. Turn it on, then reload the page.",
  developers: "Developers: suggestions and bug reports are welcome as GitHub issues.",
  developersLink: "Open an issue on GitHub",
} as const;

/** "1,950 letters left". */
export function lettersLeft(length: number): string {
  const left = MAX_MESSAGE - length;
  if (left < 0) return `${(-left).toLocaleString("en")} letters too many`;
  return `${left.toLocaleString("en")} ${left === 1 ? "letter" : "letters"} left`;
}
