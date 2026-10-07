/**
 * Every word on the account page (#434). Plain words; every message says what to do next.
 */
import { byId, data, formatPeriod, formatPrice, type PlanId } from "./pricing";

const extra = `${data.extraHours.hours} hours for ${formatPrice(data.extraHours.priceCents)}`;

/** "Oct 14", in the reader's own language and time zone. */
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
      : `Need more now? Tap Change plan to add ${extra}.`;
  return again ? `${again} ${more}` : more;
}

export const planName = (id: PlanId): string => byId[id].name;

const noFreeSlot =
  "Their plan is full. Ask them to move to a bigger plan, or pick someone else.";

export const text = {
  heading: "My account",
  loading: "Loading your account…",

  // Signed out
  signInLead: "Sign in with Discord to see your plan, your hours and your campaigns.",
  signIn: "Sign in with Discord",
  signInNote: "DMbot only asks Discord for your name, your email and your list of servers.",
  startTryItFree: "Start Try It, free",
  startTryItSignIn: "Free for 30 days. No card needed. You'll sign in with Discord first.",
  confirmAfterSignIn: "You'll sign in with Discord, then confirm your plan.",
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
    "not-allowed": "You can't do that here. Only the DM who runs the campaign can.",
    "no-free-slot": noFreeSlot,
  } as Record<string, string>,
  install: {
    done: "Done! DMbot is in your server. In Discord, type /dmbot start to begin.",
    failed: "DMbot wasn't added. Tap Add DMbot to try again.",
    not_allowed: "You can only add DMbot to a server you run. Ask its owner to add DMbot.",
    already_linked:
      "DMbot is in your server. Someone else is listed as the one who added it.",
  } as Record<string, string>,
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
    "This deletes your account and your plan, and stops payments. Your players lose any campaign you run.",
    "First, hand over or save your campaigns. To save one, type /dmbot backup in Discord.",
    "Backups people already have stay, and so do lines you said in other people's games.",
  ],
  deleteHandOverLink: "Hand over a campaign first",
  deleteNext: "Delete everything",
  keep: "Keep my account",
  deleteSure: "Last check: your campaigns and account go for good. This can't be undone.",
  deleteConfirm: "Yes, delete it all now",
  deleted: "Done. Your account and data are deleted. You can close this page.",
};
