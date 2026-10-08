/**
 * Every word on the account page (#434). Plain words; every message says what to do next.
 */
import { byId, data, formatPeriod, formatPrice, type PlanId } from "./pricing";

const extra = `${data.extraHours.hours} hours for ${formatPrice(data.extraHours.priceCents)}`;

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
      : `Need more now? Tap Change plan, then add ${extra}.`;
  return again ? `${again} ${more}` : more;
}

export const planName = (id: PlanId): string => byId[id].name;

const noFreeSlot =
  "Their plan is full. Ask them to move to a bigger plan, or pick someone else.";

export const text = {
  heading: "My Account",
  loading: "Loading your account…",

  // Signed out
  signInLead: "Sign in with Discord to see your plan, your hours and your campaigns.",
  signIn: "Sign in with Discord",
  signInNote: "DMbot only asks Discord for your name, your email and your list of servers.",
  startTryItFree: "Start Try It, free",
  startTryItSignIn:
    "Free for 30 days. No card needed. You'll sign in with Discord first, then confirm your plan.",
  haveAccount: "Already have a plan?",
  seePrices: "See the plans",
  signInFailed: "You didn't finish signing in. Tap Sign in with Discord to try again.",

  // Errors
  down: "DMbot isn't answering right now. Tap Try again in a minute.",
  tryAgain: "Try again",
  actionFailed: "That didn't work. Try again in a minute.",
  signInAgain: "For your safety, sign in again first. Tap Sign in with Discord.",
  errors: {
    "try-it-used": "You've already used Try It. Pick a plan to keep playing.",
    "has-plan": "You already have a plan. Tap Change plan to switch.",
    "payments-off": "Paying for a plan isn't open yet. Please try again soon.",
    "already-linked":
      "Someone else already said they added DMbot here. If that's wrong, ask your server's owner for help.",
    "not-installed": "DMbot isn't in this server yet. Tap Add DMbot first.",
    "not-allowed": "That didn't work. Reload this page and try again.",
    "no-free-slot": noFreeSlot,
    "offer-gone": "That offer has ended. We've updated this page.",
    "confirm-again": "That took too long. Tap Start deleting again.",
    "no-paid-plan":
      "Your plan has changed since this page opened, so we've updated it. If nothing looks different, write to us for help.",
  } as Record<string, string>,
  install: {
    done: "Done! DMbot is in your server. In Discord, type /dmbot start to begin.",
    failed: "DMbot wasn't added. Tap Add DMbot to try again.",
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

  // Plan
  planHeading: "Your plan",
  noPlan: "You don't have a plan yet.",
  startTryIt: "Start Try It",
  startTryItNote: "Free for 30 days. No card needed.",
  orPick: "Or pick a plan:",
  choose: (id: PlanId): string => `Choose ${planName(id)}`,
  changePlan: "Change plan",
  pickPlan: "Pick a plan",
  hoursBarLabel: "Hours used this month",
  grace: (date: string | null): string =>
    date
      ? `Your last payment didn't go through. Fix it by ${shortDate(date)} to keep your plan.`
      : "Your last payment didn't go through. Fix it soon to keep your plan.",
  fixPayment: "Fix my payment",
  lapsed: `Your plan has stopped. Your campaigns are kept for ${formatPeriod(data.keepAfterPlanStopsPaying)}. Pick a plan to play again.`,

  // Campaigns
  campaignsHeading: "Your campaigns",
  noCampaigns: "No campaigns yet. Start one in Discord with /dmbot start.",
  lastPlayed: (iso: string): string => `Last played ${shortDate(iso)}`,
  notPlayed: "Not played yet",
  active: "Active",
  paused: "Paused",
  youRunIt: "Uses your hours",
  youHelp: "You help run it",
  handOver: "Hand over",

  // Hand over
  handOverQuestion: (campaign: string): string => `Who should take over ${campaign}?`,
  handOverNote:
    "They become the DM. You can't undo this; only they can hand it back. After this, it uses their hours, not yours.",
  handOverNobody:
    "Nobody can take it yet. Ask the person to sign in here and start a plan (Try It is free). Then tap Hand over again.",
  handOverConfirm: "Hand it over",
  handOverDone: (campaign: string, person: string): string =>
    `Done. ${campaign} now belongs to ${person}.`,
  noFreeSlot,
  cancel: "Cancel",

  // Hand-over offers (#614): an offer waits for the new owner's yes, for 7 days.
  offersHeading: "Campaigns offered to you",
  offerIncoming: (person: string, campaign: string, server: string): string =>
    `${person} wants to hand you the campaign ${campaign} in ${server}. It would use one of your campaign slots and your plan's hours.`,
  offerExpires: (iso: string): string => `Answer by ${shortDateTime(iso)}.`,
  accept: "Accept",
  decline: "No thanks",
  acceptNoSlot: "You have no free campaign slot. Free one, or pick a bigger plan, then accept.",
  seeMyPlan: "See my plan",
  accepted: (campaign: string): string =>
    `${campaign} is yours now. You're a DM of it in Discord too.`,
  declined: (person: string): string => `Done. We'll tell ${person} in Discord.`,
  offerOutgoing: (person: string, iso: string): string =>
    `Hand-over offered to ${person}, expires ${shortDateTime(iso)}.`,
  withdraw: "Withdraw",
  withdrawn: "Done. The offer is withdrawn.",

  // Servers
  serversHeading: "Add DMbot to a server",
  serversNote: "Servers you run. Tap Add DMbot to bring it to one.",
  linkServer: "This is mine",
  linkNote: "DMbot is here, but we don't know who added it. If it was you, tap This is mine.",
  youAddedIt: "You added DMbot here",
  noServers: "You don't run any Discord servers. Ask a server's owner to add DMbot.",
  addTo: "Add DMbot",
  alreadyThere: "Already added",

  // Delete
  deleteHeading: "Delete my account and data",
  deleteStart: "Start deleting",
  /** Three short lines, the consequence first. */
  deleteWarning: [
    "This deletes your account and your plan. Your players lose any campaign you run.",
    "First, hand over or save your campaigns. To save one, type /dmbot backup in Discord.",
    "Backups people already have stay, and so do lines you said in other people's games.",
  ],
  /** Shown with the warning when a paid plan would otherwise renew (#435: cancelled at the
   * end of the paid period). */
  deletePlanStops:
    "You won't be charged again. Money you've already paid isn't paid back. If you want to ask about that, do it before you delete (see the Refunds page).",
  deleteRefundsLink: "Read the Refunds page",
  deleteHandOverLink: "Hand over a campaign first",
  deleteNext: "Delete everything",
  keep: "Keep my account",
  deleteSure: "Last check: your campaigns and account go for good. This can't be undone.",
  deleteConfirm: "Yes, delete it all now",
  deleted: "Done. Your account and data are deleted. You can close this page.",
};
