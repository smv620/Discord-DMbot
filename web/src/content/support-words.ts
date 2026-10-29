// The words about DMbot's helper answering questions sent to the team (#1432; owner decision
// 2026-10-16, built in #1431): every question gets an automatic "we got it" email from
// info@getdmbot.com, DMbot's helper (an AI) answers first, and a person takes over when the helper
// cannot help or the person asks. Replies by email join the same question.
//
// SUPPORT_AI_LIVE is false until #1431 is deployed and switched on: the privacy page and the
// Support form must not describe a helper that is not running, or send a person's words to an AI
// company on the site's say-so alone. HOW IT TURNS ON: when dev1 has deployed #1431 and it has
// answered a real question, set it to true here in the same change that moves the What's new entry
// (if any) out of `held`; a test pins the words for both states.
import { aiTrainingSentence } from "./legal";

export const SUPPORT_AI_LIVE = false;

/** The Support form's note under the box. */
export const supportNote = (live: boolean): string =>
  live
    ? `Your message goes to DMbot's helper first. The helper is an AI from Anthropic. It is sent your subject, your message and the name of your plan, and it writes the first answer. ${aiTrainingSentence} If it can't help, or you ask for a person, a person on the team takes over and reads it. We email you from info@getdmbot.com to say we got it. We keep your message and the answers for 12 months, then delete them. Deleting your account deletes them too.`
    : "Your message goes privately to the DMbot team, and is not posted anywhere public. We keep it for 12 months, then delete it. Deleting your account deletes it too.";

/** The privacy page's "When you write to the team" paragraph, as it reads while the helper is on.
 * The triggers for a person taking over (can't help, unhappy, asks for a person, 5 answers) are
 * #1431's; confirm them against its code before this is switched on. */
export const privacyParagraph = (): string =>
  `From your account page, you can write to the DMbot team. The message is not posted anywhere public. When you send it, you get an email from info@getdmbot.com to say we got it. That email does not repeat your words. DMbot's helper, an AI, answers first. It is sent your subject, your message and the name of your plan, and it goes to Anthropic, the AI company named below. ${aiTrainingSentence} A person on the team takes over and replies if the helper can't help, if you say you are unhappy, if you ask for a person, or after the helper has answered 5 times. The team reads the question and the answers then, and gets an email to tell them. When you reply to our emails, your reply is added to the same question. Your account page shows each question's subject, the date you sent it, whether it is waiting, answered or closed, and the first line of the last reply. You can open a question there to read the whole conversation: your message, the helper's answers, your emailed replies and the team's. On our website, only you and the team can read it. We keep the question and the answers for 12 months, then delete them. Until then you can read them on your account page. If you delete your account, we delete your messages and the answers too.`;

/** Added to the Anthropic line under "Companies that help us" while the helper is on. */
export const anthropicAddition =
  " It also writes the first answer to a question sent to the team. For that it is sent the subject, the message and the plan's name.";

/** Under "Getting help" on the Help page, while the helper is on (#1460). Four short sentences. */
export const helpNote =
  "We email you at once to say we have your question. DMbot's AI helper (a program, not a person) answers first, by email. A person on the team takes over if the helper can't help, or if you ask for one. Your account page shows the first line of the last reply.";
