/**
 * Every word on the pricing page, in one place (#432).
 *
 * The numbers live in plans.json (owner decisions, #432 and #437), which core can read too.
 * The words here are built from those numbers, so a number changes in one place only.
 * Keep this free of page markup: the bot and the web API's /me will reuse the wording.
 */
import facts from "./plans.json";

export type PlanId = "try-it" | "table" | "two-tables" | "guild" | "pro";

interface Period {
  count: number;
  unit: "day" | "month" | "year";
}

interface PlanFacts {
  name: string;
  /** Monthly price in US cents; 0 is free; null means no price shown yet. */
  priceCents: number | null;
  /** DMbot listening hours in a month. */
  hoursPerMonth: number;
  aboutHoursPerWeek: number | null;
  /** Most campaigns that can be active at once. */
  campaigns: number;
  trialDays: number | null;
  backups: boolean;
  keepAfterLastSession: Period;
  firstMonthAfterTrialCents: number | null;
}

interface Facts {
  order: PlanId[];
  plans: Record<PlanId, PlanFacts>;
  extraHours: { hours: number; priceCents: number };
  /** Days to fix a failed payment before the plan stops (owner decision, #437). */
  paymentGraceDays: number;
  keepAfterPlanStopsPaying: Period;
  deletionWarningDaysBefore: number[];
  recommended: PlanId;
}

/** The plan file, checked against its shape when the site is built (and in the tests). */
export const data: Facts = facts as Facts;

export interface Plan extends PlanFacts {
  id: PlanId;
  /** The hours line, directly under the price. */
  hoursLine: string;
  campaignsLine: string;
  /** Anything else this plan needs to say, or null. */
  note: string | null;
  /** Button text; null when the plan can't be picked yet. */
  action: string | null;
  recommended: boolean;
}

/** "$8.99" from 899. */
export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/** "60 days", "6 months", "1 year". */
export function formatPeriod({ count, unit }: Period): string {
  return `${count} ${unit}${count === 1 ? "" : "s"}`;
}

function hoursLine(p: PlanFacts): string {
  return p.aboutHoursPerWeek === null
    ? `${p.hoursPerMonth} hours a month`
    : `About ${p.aboutHoursPerWeek} hours a week (${p.hoursPerMonth} hours a month)`;
}

function campaignsLine(p: PlanFacts): string {
  return p.campaigns === 1 ? "1 campaign" : `Up to ${p.campaigns} campaigns`;
}

const tryItName = data.plans["try-it"].name;

function note(p: PlanFacts): string | null {
  if (p.trialDays !== null) return `Free for ${p.trialDays} days. No backups or downloads.`;
  if (p.firstMonthAfterTrialCents !== null) {
    return `First month ${formatPrice(p.firstMonthAfterTrialCents)} after ${tryItName}.`;
  }
  if (p.priceCents === null) {
    return `Need more now? Pick ${data.plans.guild.name} and add extra hours.`;
  }
  return null;
}

function action(p: PlanFacts): string | null {
  if (p.priceCents === null) return null;
  if (p.priceCents === 0) return "Try it free";
  return `Choose ${p.name}`;
}

/** Every plan by id. */
export const byId = Object.fromEntries(
  data.order.map((id) => {
    const p = data.plans[id];
    const plan: Plan = {
      ...p,
      id,
      hoursLine: hoursLine(p),
      campaignsLine: campaignsLine(p),
      note: note(p),
      action: action(p),
      recommended: id === data.recommended,
    };
    return [id, plan];
  }),
) as Record<PlanId, Plan>;

export const tryIt = byId["try-it"];
/** The three paid plans shown as cards, in order. */
export const plans: readonly Plan[] = [byId.table, byId["two-tables"], byId.guild];
export const pro = byId.pro;

/** The owner's headline, kept word for word; split so the first sentence can be the heading. */
export const headline = {
  title: "Pick the hours your table plays.",
  rest: "Every plan has everything. Change plans any time.",
};

/** The Try It strip: "8 hours a month, 1 campaign. Free for 30 days. No backups or downloads." */
export const tryItLine = `${tryIt.hoursLine}, ${tryIt.campaignsLine.toLowerCase()}. ${tryIt.note ?? ""}`;

export const extraHours = {
  name: "Extra hours",
  priceCents: data.extraHours.priceCents,
  hours: data.extraHours.hours,
  /** "$4.99 for 10 hours" */
  priceLine: `${formatPrice(data.extraHours.priceCents)} for ${data.extraHours.hours} hours`,
  line: `Run out? Add ${data.extraHours.hours} hours from My account. They last until the end of this month.`,
};

/** What every plan does, after "every plan": one list, used twice below. */
const featureList =
  "writes down what’s said at the table, remembers the names your table makes up, helps with rules, remembers your story, and keeps backups";
const features = `Every plan ${featureList}.`;

export const labels = {
  recommendedBadge: "Best for one game a week",
  perMonth: "a month",
  free: "Free",
  comingSoon: "Coming soon",
  plansRegion: "Plans. Scroll sideways to see them all.",
  morePlansHeading: "Bigger tables and extra hours",
  faqHeading: "Questions",
  swipeHint: "Swipe to see every plan.",
  everyPlanHas: features,
  tryItHeading: "New here? Try it free.",
};

export interface Question {
  question: string;
  answer: string;
}

const keep = (id: PlanId): string => formatPeriod(byId[id].keepAfterLastSession);
const [firstWarning, lastWarning] = data.deletionWarningDaysBefore;

/** The plan rules, said plainly (#432). */
export const questions: readonly Question[] = [
  {
    question: "What counts as an hour?",
    answer:
      "The time DMbot is listening, from when you start it to when you stop it. Time when DMbot isn't listening doesn't count.",
  },
  {
    question: "Do unused hours carry over?",
    answer: `No. Your hours are for the month. Next month you get a full set again. If you run out, you can add ${extraHours.hours} more hours.`,
  },
  {
    question: "What's different between the plans?",
    answer: `Only the hours and how many campaigns you can run. The free ${tryItName} has no backups or downloads; everything else is the same on every plan: it ${featureList}.`,
  },
  {
    question: "Can I change my plan?",
    answer: "Yes, any time, from My account.",
  },
  {
    question: "What if I move to a plan with fewer campaigns?",
    answer:
      "Nothing is deleted. The first campaigns you play after the change keep going, up to your new limit. The rest pause. To play a paused one, move to a bigger plan.",
  },
  {
    question: "What if a payment doesn't go through?",
    answer: `Your plan keeps working for ${data.paymentGraceDays} days while you fix it. Tap Fix my payment in My account.`,
  },
  {
    question: "How long do you keep my campaign?",
    answer: `After your last game: ${keep("try-it")} on ${tryItName}, ${keep("table")} on ${byId.table.name}, and ${keep("two-tables")} on the other plans. If you stop paying, we keep it for ${formatPeriod(data.keepAfterPlanStopsPaying)}. We message you on Discord ${firstWarning} days and ${lastWarning} days before anything is deleted.`,
  },
  {
    question: "Can I give a campaign to someone else?",
    answer:
      "Yes. In My account, tap Hand over next to the campaign and pick someone whose plan has room for it. From then on it uses their hours.",
  },
  {
    question: "Can I delete everything?",
    answer:
      "Yes. Ask from My account and we delete it all. Backups other people already downloaded are theirs to delete.",
  },
];
