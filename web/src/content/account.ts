/**
 * Every word on the account page (#434). Plain words; every message says what to do next.
 */
import { byId, type PlanId } from "./pricing";

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

export function hoursLeftLine(used: number, cap: number, renewsOn: string | null): string {
  if (used >= cap) {
    return renewsOn
      ? `Your hours start again on ${shortDate(renewsOn)}. Need more now? Add 10 hours.`
      : "Need more now? Add 10 hours.";
  }
  return renewsOn ? `Your hours start again on ${shortDate(renewsOn)}.` : "";
}

export const planName = (id: PlanId): string => byId[id].name;

export const text = {
  heading: "My account",
  loading: "Loading your account…",

  // Signed out
  signInLead: "Sign in with Discord to see your plan, your hours and your campaigns.",
  signIn: "Sign in with Discord",
  signInNote: "DMbot only asks Discord for your name, your email and your list of servers.",
  newHere: "New here? Sign in, then start Try It. It's free and needs no card.",
  seePrices: "See the plans",
  signInFailed: "You didn't finish signing in. Tap Sign in with Discord to try again.",

  // Errors
  down: "We can't reach DMbot right now. Try again in a minute.",
  tryAgain: "Try again",
  actionFailed: "That didn't work. Try again in a minute.",
  signedOutNow: "You've been signed out. Sign in again to carry on.",

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
  grace: (date: string): string =>
    `Your last payment didn't go through. Fix it by ${shortDate(date)} to keep your plan.`,
  fixPayment: "Fix my payment",
  lapsed:
    "Your plan has stopped. Your campaigns are kept for 120 days. Pick a plan to play again.",

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
    "They need a plan with room for one more campaign. After that, it uses their hours, not yours.",
  handOverNobody:
    "Nobody can take it yet. The person needs a plan with room for one more campaign.",
  handOverConfirm: "Hand it over",
  handOverDone: (campaign: string, person: string): string =>
    `Done. ${campaign} now belongs to ${person}.`,
  noFreeSlot: "Their plan is full. Ask them to pause a campaign or pick a bigger plan.",
  cancel: "Cancel",

  // Servers
  serversHeading: "Add DMbot to a server",
  serversNote: "These are the Discord servers you can add DMbot to.",
  noServers: "You don't run any Discord servers. Ask a server's owner to add DMbot.",
  addTo: "Add DMbot",
  alreadyThere: "DMbot is here",

  // Delete
  deleteHeading: "Delete my account and data",
  deleteStart: "Delete my account and data",
  deleteWarning:
    "This deletes your account, your plan and the campaigns you run, and stops any payments. Backups that people already downloaded are not deleted. Lines you said in other people's campaigns stay there.",
  deleteNext: "Delete everything",
  keep: "Keep my account",
  deleteSure: "Are you sure? This can't be undone.",
  deleteConfirm: "Yes, delete it all now",
  deleted: "Done. Your account and data are deleted. You can close this page.",
};
