/**
 * Every word on the account page (#434). Plain words; every message says what to do next.
 */
import { SUPPORT_AI_LIVE, supportNote } from "./support-words";
import { byId, data, formatPeriod, formatPrice, type PlanId } from "./pricing";

const extraHours = data.extraHours.hours;
const extraPrice = formatPrice(data.extraHours.priceCents);

/** "Oct 14", in the reader's own language and time zone. */
/** A day and a time, for deadlines that end partway through a day ("Oct 14, 3:00 PM"). */
export function shortDateTime(iso: string, locale?: string): string {
  return new Date(iso).toLocaleString(locale, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function shortDate(iso: string, locale?: string): string {
  return new Date(iso).toLocaleDateString(locale, {
    month: "short",
    day: "numeric",
    ...(iso.length > 10 ? {} : { timeZone: "UTC" }),
  });
}

/** "About 11 of 18 hours used". */
export function hoursUsedLine(used: number, cap: number): string {
  const rounded = Math.round(used);
  if (used <= 0) return `None of your ${cap} hours used yet`;
  if (used >= cap) return `All ${cap} hours used`;
  if (rounded === 0) return `Less than 1 of ${cap} hours used`;
  return `About ${Math.min(rounded, cap - 1)} of ${cap} hours used`;
}

export function hoursLeftLine(
  used: number,
  cap: number,
  renewsOn: string | null,
  plan: PlanId,
): string {
  const again = renewsOn ? `Your hours start again on ${shortDate(renewsOn)}.` : "";
  if (used < cap) return again;
  const more =
    plan === "try-it"
      ? "Pick a plan below to keep playing."
      : `Need more now? Tap Add ${extraHours} hours below.`;
  return again ? `${again} ${more}` : more;
}

export const planName = (id: PlanId): string => byId[id].name;

/** The public place for questions (the GitHub Discussions category QAbot answers, #1339). */
export const QA_URL = "https://github.com/smv620/Discord-DMbot/discussions/categories/q-a-for-dmbot";

export const text = {
  heading: "My Account",
  loading: "Loading your account…",

  // Signed out
  signedOutHeading: "You're not signed in.",
  signedOutLead: "Sign in to see your plan, your hours and your campaigns.",
  newHeading: "New to DMbot?",
  signIn: "Sign in with Discord",
  signInNote: "DMbot only asks Discord for your name, your email and your list of servers.",
  startTryItFree: "Start Try It, free",
  startTryItSignIn:
    "Free for 30 days. No card needed. You'll sign in with Discord first, then confirm your plan.",
  seePrices: "See the plans",
  signInFailed: "You didn't finish signing in. Tap Sign in with Discord to try again.",
  signInMoved:
    "This Discord account's DMbot account moved to a new Discord account. Sign in with the new one.",
  tooManyTries: "Too many tries. Wait about 10 minutes, then tap Sign in with Discord again.",

  // Sign in by an emailed link (#1231)
  emailHeading: "Or get a sign-in link by email",
  emailLabel: "Your email address",
  emailButton: "Email me a sign-in link",
  emailSending: "One moment…",
  emailNote:
    "The link works once and ends after 15 minutes. To add DMbot to a server, sign in with Discord instead.",
  emailMissing: "Type your email address first.",
  emailAnswer:
    "If that email belongs to a DMbot account, we've sent a link. Check your inbox (and spam). It ends in 15 minutes.",
  emailWait: "Too many tries. Wait about 10 minutes, then try again.",
  emailLinkWait: "Too many tries. Wait a minute, then open the link in your email again.",
  emailLinkBad:
    "That sign-in link didn't work. It may have been used already, or it ran out of time. Ask for a new one below.",

  // Errors
  down: "DMbot isn't answering right now. Tap Try again in a minute.",
  tryAgain: "Try again",
  actionFailed: "That didn't work. Try again in a minute.",
  signInAgain: "For your safety, sign in again first. Tap Sign in with Discord.",
  errors: {
    "bad-email": "That doesn't look like an email address. It should look like name@example.com. Check it and try again.",
    "try-it-used": "You've already used Try It. Pick a plan to keep playing.",
    "has-plan": "You already have a plan. Tap Change plan to switch.",
    "not-ending": "This plan isn't ending, or has already ended. The page now shows how things are.",
    "use-billing-page":
      "We can't renew it from here. Tap Change plan and renew on our payment company's page.",
    "payments-off": "Paying for a plan isn't open yet. Please try again soon.",
    "already-linked":
      "Someone else already said they added DMbot here. If that's wrong, ask your server's owner for help.",
    "not-installed": "DMbot isn't in this server yet. Tap Add DMbot first.",
    "not-allowed": "That didn't work. Reload this page and try again.",
    // Only ever the person offered: an offer is never refused for "no plan" (#437).
    "no-free-slot":
      "You need a DMbot plan with room for one more campaign. Tap See my plan, then tap Accept again.",
    "offer-gone": "That offer has ended. We've updated this page.",
    "confirm-again": "That took too long. Tap Start deleting again.",
    "slow-down":
      "Too many tries. Wait about 10 minutes, then try again. (Deleting an account can need an hour.)",
    // Only an out-of-date page gets this, so the page updates itself (like no-paid-plan).
    "free-access": "You have free access, so there's nothing to pay. We've updated this page.",
    "no-paid-plan":
      "Your plan has changed since this page opened, so we've updated it. If nothing looks different, write to us for help.",
  } as Record<string, string>,
  install: {
    done: "Done! DMbot is in your server. In Discord, type /dmbot start and pick it from the list.",
    failed: "DMbot wasn't added. Tap Add DMbot to try again.",
    slow: "Too many tries. Wait about 10 minutes, then tap Add DMbot again.",
    not_allowed: "You can only add DMbot to a server you run. Ask its owner to add DMbot.",
    already_linked:
      "DMbot is in your server. Someone else is listed as the one who added it.",
    other_account:
      "DMbot was added, but by a different Discord account than the one signed in here. If that was you, tap This is mine next to your server below.",
  } as Record<string, string>,
  installSignedOut:
    "You were signed out. Sign in with Discord again, then find your server below. Tap This is mine if it's there, or Add DMbot if not.",
  notTheDm: "You can't do that here. Only the DM who runs the campaign can.",
  signedOutNow: "You've been signed out. Tap Sign in with Discord to carry on.",
  busy: "One moment…",

  greeting: (name: string): string => `Hi, ${name}.`,
  signOut: "Sign out",
  signOutEverywhere: "Sign out everywhere",
  signOutEverywhereNote: "Ends your sign-in on every computer and phone, this one too.",
  signOutEverywhereWait: "Wait a minute, then try again.",

  // Plan
  planHeading: "Your plan",
  noPlan: "You don't have a plan yet.",
  startTryIt: "Start Try It",
  startTryItNote: "Free for 30 days. No card needed.",
  orPick: "Or pick a plan:",
  choose: (id: PlanId): string => `Choose ${planName(id)}`,
  addHours: `Add ${extraHours} hours (${extraPrice})`,
  addHoursNote: `You pay once, on our payment company's page. These ${extraHours} hours are for this month only and don't roll over.`,
  changePlan: "Change plan",
  // The plan line says what the plan is doing, not only its name (#2165). Dates are the site's usual words.
  planRenews: (plan: string, iso: string): string => `${plan} (renews on ${shortDate(iso)})`,
  planEndsLine: (plan: string, iso: string): string => `${plan} (ends on ${shortDate(iso)})`,
  planChanging: (plan: string, next: string, iso: string): string =>
    `${plan} (new plan: ${next} starts on ${shortDate(iso)})`,
  planExpired: (plan: string, iso: string): string => `Your ${plan} plan ended on ${shortDate(iso)}.`,
  planExpiredNoDate: (plan: string): string => `Your ${plan} plan has ended.`,
  // Renew undoes a cancellation that hasn't taken effect, after one plain question.
  renew: "Renew",
  renewAsk: (plan: string, iso: string): string =>
    `Keep your ${plan} plan? It renews on ${shortDate(iso)}, as before. Nothing is charged before then.`,
  renewAskNoDate: (plan: string): string =>
    `Keep your ${plan} plan? It renews as before. Nothing is charged before the renewal date.`,
  renewYes: "Yes, renew",
  renewNo: "Not now",
  renewed: (plan: string, iso: string | null): string =>
    iso ? `Done. Your ${plan} plan renews on ${shortDate(iso)}.` : `Done. Your ${plan} plan will renew as before.`,
  endingNote: "You won't be charged again unless you tap Renew.",
  changePlanNote: "Opens our payment company's page, on the plans.",
  pickPlan: "Pick a plan",
  hoursBarLabel: "Hours used this month",
  grace: (date: string | null): string =>
    date
      ? `Your last payment didn't go through. Fix it by ${shortDate(date)} to keep your plan.`
      : "Your last payment didn't go through. Fix it soon to keep your plan.",
  fixPayment: "Fix my payment",
  // A first payment has no grace: nothing started, so there is nothing to fix, only to retry (#922).
  firstPaymentFailed:
    "Your first payment didn't go through, so you have no plan yet. Pick a plan below to try again.",
  // Free access (#806): never why someone has it.
  freeAccess: "Free access",
  freeAccessUntil: (iso: string): string => `Free access until ${shortDate(iso)}`,
  // Not "you're still paying": after Stop paying the plan runs out its period, so that
  // would become false. The date is the plan's own end (its next renewal if left alone).
  stillPaying: (plan: string | null, endsOn: string | null): string => {
    const name = plan ? `Your ${plan} plan` : "Your plan";
    // Stop paying opens the payment company's page, where the plan is cancelled (#824).
    return endsOn
      ? `${name} runs until ${shortDate(endsOn)}. Tap Stop paying, then cancel on the payment page, if you don't need it.`
      : `${name} is still being paid for. Tap Stop paying, then cancel on the payment page, if you don't need it.`;
  },
  /** A plan that was cancelled and works to its end: nothing more to stop (#824). */
  planEnds: (plan: string | null, iso: string): string =>
    `${plan ? `Your ${plan} plan` : "Your plan"} ends on ${shortDate(iso)}. You won't be charged again.`,
  /** Hours for free access with a cap (#824); renewal only, since free people buy no more. */
  freeHoursLeft: (used: number, cap: number, iso: string | null): string => {
    if (used < cap) return iso ? `Your hours start again on ${shortDate(iso)}.` : "";
    // Out of hours: free people can't buy more, so say when they come back, or where to ask.
    return iso
      ? `You're out of hours until ${shortDate(iso)}. Then they start again.`
      : "You're out of hours for now. Write to us if you need more.";
  },
  stopPaying: "Stop paying",
  freeNext: "In Discord, type /dmbot start and pick it from the list to play.",
  // Only without a paid plan: with one, the plan's own date is above, and what happens
  // after the grant ends depends on whether they stop paying.
  freeEnds: (iso: string): string => `After ${shortDate(iso)}, pick a plan to keep playing.`,
  lapsed: `Your campaigns are kept for ${formatPeriod(data.keepAfterPlanStopsPaying)}. Pick a plan to play again.`,

  // Campaigns
  campaignsHeading: "Your campaigns",
  noCampaigns: "No campaigns yet. Start one in Discord with /dmbot start.",
  /** The button under a long list (#1991): the real count, one or many. */
  showOlder: (n: number) => (n === 1 ? "Show 1 older campaign" : `Show ${n} older campaigns`),
  showFewer: "Show fewer",
  lastPlayed: (iso: string): string => `Last played ${shortDate(iso)}`,
  notPlayed: "Not played yet",
  active: "Active",
  paused: "Paused",
  youRunIt: "Uses your hours",
  // Free access with no limits has no hours to use (#824).
  youRunItFree: "You run it",
  youHelp: "You help run it",
  handOver: "Hand over",

  // Hand over
  handOverQuestion: (campaign: string): string => `Who should take over ${campaign}?`,
  handOverNote:
    "Nothing changes until they say yes. They have 7 days to answer. If they say yes, it uses their hours, not yours, and you stay on as a DM.",
  handOverNoteFree:
    "Nothing changes until they say yes. They have 7 days to answer. If they say yes, it becomes theirs and you stay on as a DM.",
  handOverNobody:
    "Only someone who helps run this campaign can take it over, and nobody does yet. Add them as a DM in Discord, then tap Hand over again.",
  handOverConfirm: "Offer it",
  handOverDone: (person: string): string =>
    `Done. We've asked ${person}. You'll see their answer here.`,
  cancel: "Cancel",

  // Hand-over offers (#614): an offer waits for the new owner's yes, for 7 days.
  offersHeading: "Campaigns offered to you",
  offerIncoming: (person: string, campaign: string, server: string): string =>
    `${person} wants to hand you ${campaign} in ${server}. If you accept, it uses one of your campaign slots and your plan's hours, and ${person} stays on as a DM. Tap Accept or No thanks.`,
  /** A capped grant has hours but no "plan" to name (#824). */
  offerIncomingCapped: (person: string, campaign: string, server: string): string =>
    `${person} wants to hand you ${campaign} in ${server}. If you accept, it uses one of your campaign slots and your hours, and ${person} stays on as a DM. Tap Accept or No thanks.`,
  offerIncomingFree: (person: string, campaign: string, server: string): string =>
    `${person} wants to hand you ${campaign} in ${server}. If you accept, it becomes yours and ${person} stays on as a DM. Tap Accept or No thanks.`,
  offerExpires: (iso: string): string => `Answer by ${shortDateTime(iso)}.`,
  accept: "Accept",
  decline: "No thanks",
  // Shown only to the person offered: the plan check happens at Accept, never when the
  // offer is made, so the owner never learns whether someone pays (#437, PLAN).
  acceptNoSlot:
    "To take this campaign you need a DMbot plan with room for one more campaign. Tap See my plan to pick one, then tap Accept again. The offer stays open until the date above.",
  // Free access has no plans to pick (#806), and never says why someone has it.
  acceptNoSlotFree:
    "You have no room for another campaign. Hand one of yours over below, or delete one, then tap Accept again.",
  seeMyPlan: "See my plan",
  // The bot tells the other person in Discord whenever an offer is answered or taken back
  // on the site (#794, #797), so the words promise it plainly (#798).
  accepted: (campaign: string, person: string): string =>
    `${campaign} is yours now. You're a DM of it in Discord too. We'll tell ${person} in Discord.`,
  declined: (person: string): string => `Done. The offer is closed. We'll tell ${person} in Discord.`,
  offerOutgoing: (person: string, iso: string): string =>
    `Waiting for ${person} to say yes (until ${shortDateTime(iso)}). Nothing changes until they do.`,
  withdraw: "Take back offer",
  withdrawn: (person: string): string =>
    `Done. You took the offer back. We'll tell ${person} in Discord.`,

  // Servers
  serversHeading: "Add DMbot to a server",
  serversNote: "Servers you run. Tap Add DMbot to bring it to one.",
  linkServer: "This is mine",
  linkNote: "DMbot is here, but we don't know who added it. If it was you, tap This is mine.",
  youAddedIt: "You added DMbot here",
  noServers: "You don't run any Discord servers. Ask a server's owner to add DMbot.",
  addTo: "Add DMbot",
  alreadyThere: "Already added",

  // My characters (#1164): read-only. Changing a sheet stays in Discord.
  charactersHeading: "My characters",
  charactersNone: "No characters yet. In Discord, press 📜 My character sheet in your campaign.",
  characterUnread: "Sheet saved",
  characterUnreadNote: "Its details show up after DMbot reads it.",
  characterWhere: (server: string, campaign: string | null): string =>
    campaign ? `${server} · ${campaign}` : server,
  characterLevel: (n: number): string => `Level ${n}`,
  characterHp: (n: number): string => `Max hit points: ${n}`,
  characterAc: (n: number): string => `Armor class (AC) ${n}`,
  characterRead: (date: string): string => `Read on ${shortDate(date)}`,
  characterTyped: "You typed this one in.",
  characterOpen: "Open the online sheet (new tab)",
  characterChange: "Change a character in Discord with 📜 My character sheet.",
  // Where DMbot may record you (#1151). The warning is the bot's own wording.
  recordingHeading: "Where DMbot may record you",
  recordingLine: (server: string, date: string): string =>
    `${server}: DMbot may record you. You said yes on ${shortDate(date)}.`,
  recordingOld: (server: string): string =>
    `${server}: not recording you. To allow it, press 🗂️ Menu in DMbot's private message in Discord.`,
  stopRecording: "Stop recording me",
  stopWarning: (server: string): string =>
    `Stop recording you in ${server}? DMbot won't write down anything you say from now on. The campaign's record will have gaps wherever you speak, so its summaries can miss things and plot holes can appear. You can start again any time in Discord.`,
  stopYes: "Yes, stop recording me",
  stopKeep: "Keep recording",
  stoppedRecording: (server: string): string =>
    `Stopped. DMbot is not recording you in ${server}. To start again, press 🗂️ Menu in DMbot's private message in Discord.`,

  // My DMbot ID and experience points, and moving to a new Discord account (#1231)
  idHeading: "Your DMbot ID",
  idLine: (id: string): string => `Your DMbot ID: ${id}`,
  idNote: "It is your account number, and it shows on your invoices. It is not a password.",
  pointsLine: (n: number): string => `Experience points: ${n}`,
  pointsNote: "The minutes DMbot has run for you, all time.",
  moveHeading: "Moving to a new Discord account?",
  moveText:
    "Press Get a move code. We email it to your payment email. Your plan, hours and campaigns move to your new account. Only want to give campaigns to another game master? Use Hand over on each campaign instead. They use their own plan.",
  moveButton: "Get a move code",
  moveSent:
    "We've emailed a move code to your payment email. It works once, for 30 minutes. On your new Discord account, type /dmbot migrate, press Import my account, and type the code. You will be asked to say yes to being recorded again.",
  moveNotReady: "Moving to a new Discord account isn't open yet. Please try again soon.",
  moveWait: "Too many tries. Wait an hour, then try again.",
  moveNoEmail:
    "We don't have the email you pay with yet. It's saved when a payment goes through. Try again later, or tell us on the Say hello page.",
  moveTooSoon:
    "You can move once every 30 days, and this account moved less than 30 days ago. Try again after 30 days, or tell us on the Say hello page.",
  moveEmailFailed: "The code couldn't be emailed just now. Try again in a few minutes.",

  // The email DMbot uses (#2349)
  myEmailHeading: "Your email",
  emailNow: (address: string): string => `DMbot emails you at ${address}.`,
  emailNone: "DMbot has no email for you yet, so it can't email you.",
  emailPayment: "That's the email on your payment account.",
  emailPaymentButton: "Change your email in Stripe",
  emailPaymentHelp: "You will leave this page. In Stripe, open Billing information, then change your email. It shows here once DMbot has the new one.",
  emailChange: "Change your email",
  emailAdd: "Add an email",
  emailNewLabel: "New email address",
  emailSend: "Send a confirmation link",
  emailPending: (address: string, old: string | null): string =>
    old
      ? `We sent a link to ${address}. Nothing changes until you open it. Until then DMbot keeps emailing ${old}. Sending a new link replaces this one.`
      : `We sent a link to ${address}. Your email is saved when you open it. Until then DMbot has no email for you. Sending a new link replaces this one.`,
  emailLinkNote: "We'll send a link to this address. Your email changes when you open it.",
  emailSendAgain: "Send a new link",
  emailLoadFailed: "Your email didn't load. Reload the page.",
  emailSaved: "If that address can be used, we've sent a link to it. Check the inbox (and spam).",
  emailTypeFirst: "Type your new email address first.",
  emailPortalOff: "The payment page isn't available right now. Try again later.",

  // Write to the team, for everyone with an account, Try It too (#1340, #1435)
  supportHeading: "Write to the DMbot team",
  supportIntro: "Something wrong with your plan, an invoice or DMbot itself? Write to us here.",
  // Only for Try It (#1435).
  supportIntroTryIt: "Questions about buying a plan are welcome too.",
  supportPrivacy: supportNote(SUPPORT_AI_LIVE),
  supportPublicLead: "A question anyone could answer? Ask it in the public",
  supportSubjectLabel: "Subject",
  supportMessageLabel: "What can we help with?",
  supportMessageHint: "Please don't type passwords or card numbers.",
  supportSend: "Send to the team",
  supportSending: "Sending…",
  supportSent: "Sent. Thanks, the team will read it. We'll reply by email to the address on your account.",
  // The account has no email: nothing on the Account page adds one (the address is the one
  // Discord gives when you sign in, or the one you pay with), so the way is through Discord.
  supportSentNoEmail:
    "Sent. Thanks, the team will read it. But we don't have an email for your account, so we can't reply. Add and confirm an email in your Discord settings, sign in here again, and write to us again.",
  supportAnother: "Write another",
  // The person's own questions under the form (#1373): what they sent and where it stands; each
  // opens to its whole conversation (#2193).
  questionsHeading: "Your questions",
  questionsLead:
    "What you have sent the team. Replies come by email, and you can read the whole conversation here.",
  // No email on the account: the team can't write back, so say what to do (as the thank-you does).
  questionsLeadNoEmail:
    "What you have sent the team. You can read the whole conversation here. We have no email for your account, so the team can't write back. Add and confirm an email in your Discord settings, then sign in here again.",
  questionSent: (date: string) => `Sent ${date}`,
  // The first line of the last reply from DMbot's AI helper or the team, under the question (#1432).
  questionLatest: (line: string) => `Last reply: ${line}`,
  // Opening a question shows the whole conversation (#2193): yours, DMbot's helper's and the team's.
  conversationOpen: "Read the conversation",
  conversationClose: "Hide the conversation",
  conversationLoading: "Loading the conversation…",
  conversationLoaded: "The conversation is shown below.",
  conversationError: "We couldn't load this conversation. Try again in a minute.",
  conversationRetry: "Try again",
  conversationOrder: "Oldest first.",
  conversationNoReply: "No reply yet. Replies come by email.",
  conversationNoReplyNoEmail: "No reply yet.",
  conversationLabel: (subject: string) => `Conversation: ${subject}`,
  conversationWho: {
    you: "You",
    helper: "DMbot's helper (a program)",
    team: "DMbot team",
  },
  questionStatus: {
    new: "Waiting",
    answered: "Answered — check your email",
    // An account with no email gets no email: say only what the team did.
    answeredNoEmail: "Answered",
    // Who answered (#1432): DMbot's AI helper or a person on the team.
    answeredHelper: "Answered by DMbot's AI helper — check your email",
    answeredHelperNoEmail: "Answered by DMbot's AI helper",
    answeredTeam: "Answered by the team — check your email",
    answeredTeamNoEmail: "Answered by the team",
    // A person on the team has it and will write back.
    escalated: "With the team",
    closed: "Closed",
    // For someone who can write again; others just see "Closed".
    closedWriteAgain: "Closed — you can write again",
  },
  supportErrors: {
    "ticket-empty": "Please fill in the subject and your message.",
    "subject-too-long": "The subject is too long. Please keep it to 120 letters or fewer.",
    "ticket-too-long": "That's too long. Please keep it to 4,000 letters or fewer.",
    "slow-down":
      "Too many messages right now. You can send one every 10 minutes. Try again in a few minutes. Your words are still here.",
    "not-human":
      "Tick the box that says you're a person, then press Send again. Your words are still here.",
    // No account yet (#1435): everyone with a plan, Try It too, may write.
    "not-paying":
      "We can't find a DMbot account for you, so we can't send this. Sign in again, or ask in the public Q&A for DMbot. Your words are still here.",
    "one-waiting":
      "You already have a question waiting for the team. You can send another once it's answered. Your words are still here.",
    "daily-limit":
      "Free accounts can send 3 questions a day, and you've sent 3. Try again in a day, or ask in the public Q&A for DMbot. Your words are still here.",
    "signed-out": "You were signed out. Sign in again, then send it. Your words are still here.",
    network: "No connection. Check your internet, then press Send again. Your words are still here.",
    server:
      "That didn't send, and it's on our side. Your words are still here. Try again in a few minutes.",
  } as Record<string, string>,
  supportCheckAgain: "Try the check again",
  supportCheckFailed:
    "The “are you a person?” check didn't load. Reload the page or try another browser. Reloading clears what you typed, so copy it first.",
  // For everyone who can't write privately (no account yet, or an API older than #1340)
  qaHeading: "Questions about DMbot?",
  qaText:
    "Ask in the public Q&A, where anyone can read the answers. Don't post private details there. Or tell us what you think on the Say hello page.",
  qaLink: "Q&A for DMbot",
  helloLink: "Say hello",

  // Download my data (#1150)
  exportHeading: "Download my data",
  exportWhat:
    "You get one file with the things DMbot keeps about you: your account, your plan and payments, the servers where you said yes to recording, your campaign names and your character sheets. It does not include table transcripts: read those in Discord with /transcript.",
  exportButton: "Download my data",
  exportWait:
    "The file starts downloading in a moment. You can get it once a minute.",
  exportTooSoon: "Wait a minute, then try again.",

  // Delete
  deleteHeading: "Delete my account and data",
  deleteStart: "Start deleting",
  /** Three short lines, the consequence first. */
  deleteWarning: [
    "This deletes your account and your plan.",
    "Your campaigns are kept for 120 days. Players can still read and download them. After that they're deleted, unless another DM takes one on.",
    "To keep one, hand it over first. To save a copy, type /dmbot backup in Discord. Backups people already downloaded stay with them, and so do the lines you said in other people's games.",
  ],
  /** The first warning line for someone with free access (#806): no plan, and it goes too. */
  deleteWarningFree:
    "This deletes your account and your free access. Your campaigns are kept for 120 days (players can still read and download them), then deleted unless another DM takes them on.",
  /** The same, while a paid plan is still being paid for: the payments stop too. */
  deleteWarningFreePaying:
    "This deletes your account and your free access, and stops your payments. Your campaigns are kept for 120 days (players can still read and download them), then deleted unless another DM takes them on.",
  /** Shown with the warning when a paid plan would otherwise renew (#435: cancelled at the
   * end of the paid period). */
  deletePlanStops:
    "You won't be charged again. Money you've already paid isn't paid back. If you want to ask about that, do it before you delete (see the Refunds page).",
  deleteRefundsLink: "Read the Refunds page",
  deleteHandOverLink: "Hand over a campaign first",
  deleteNext: "Delete everything",
  keep: "Keep my account",
  deleteSure:
    "Last check: your account is deleted for good and this can't be undone. Your campaigns are kept 120 days, then deleted unless another DM takes them on.",
  deleteConfirm: "Yes, delete it all now",
  deleted:
    "Done. Your account is deleted. Your campaigns stay for 120 days. To save one, one of its DMs presses 🤝 Take it on in ⚙️ Settings → 🗂️ Campaign management, in that Discord server. You can close this page.",
};
