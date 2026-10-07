/**
 * Every word and number on the pricing page, in one place (#432).
 *
 * Prices, hours, campaign caps and retention are owner decisions (#432, #437,
 * docs/PLAN.md). Don't change them here without the owner's say-so on the issue.
 * The bot and the web API will share this wording later, so keep it free of page markup.
 */

/** The owner's headline, kept word for word; split so the first sentence can be the heading. */
export const headline = {
  title: "Pick the hours your table plays.",
  rest: "Every plan has everything. Change plans any time.",
};

export type PlanId = "try-it" | "table" | "two-tables" | "guild" | "pro";

export interface Plan {
  id: PlanId;
  name: string;
  /** Monthly price in US cents; 0 is free; null means no price shown yet. */
  priceCents: number | null;
  /** DMbot listening hours in a month. */
  hoursPerMonth: number;
  /** Most campaigns that can be active at once. */
  campaigns: number;
  /** The hours line, directly under the price. */
  hoursLine: string;
  /** The campaigns line. */
  campaignsLine: string;
  /** Anything else this plan needs to say, or null. */
  note: string | null;
  /** Button text. */
  action: string;
  /** Shown as the usual pick. */
  recommended: boolean;
  comingSoon: boolean;
}

export const tryIt: Plan = {
  id: "try-it",
  name: "Try It",
  priceCents: 0,
  hoursPerMonth: 8,
  campaigns: 1,
  hoursLine: "8 hours a month",
  campaignsLine: "1 campaign, for 30 days",
  note: "No backups or downloads.",
  action: "Try it free",
  recommended: false,
  comingSoon: false,
};

/** The three plans shown as cards. */
export const plans: readonly Plan[] = [
  {
    id: "table",
    name: "Table",
    priceCents: 899,
    hoursPerMonth: 18,
    campaigns: 1,
    hoursLine: "About 4 hours a week (18 a month)",
    campaignsLine: "1 campaign",
    note: "First month $1.99 after Try It.",
    action: "Choose Table",
    recommended: true,
    comingSoon: false,
  },
  {
    id: "two-tables",
    name: "Two Tables",
    priceCents: 1799,
    hoursPerMonth: 43,
    campaigns: 2,
    hoursLine: "About 10 hours a week (43 a month)",
    campaignsLine: "Up to 2 campaigns",
    note: null,
    action: "Choose Two Tables",
    recommended: false,
    comingSoon: false,
  },
  {
    id: "guild",
    name: "Guild",
    priceCents: 3499,
    hoursPerMonth: 87,
    campaigns: 5,
    hoursLine: "About 20 hours a week (87 a month)",
    campaignsLine: "Up to 5 campaigns",
    note: null,
    action: "Choose Guild",
    recommended: false,
    comingSoon: false,
  },
];

export const pro: Plan = {
  id: "pro",
  name: "Pro",
  priceCents: null,
  hoursPerMonth: 217,
  campaigns: 20,
  hoursLine: "About 50 hours a week (217 a month)",
  campaignsLine: "Up to 20 campaigns",
  note: null,
  action: "Coming soon",
  recommended: false,
  comingSoon: true,
};

export const extraHours = {
  name: "Extra hours",
  priceCents: 499,
  hours: 10,
  line: "Run out of hours? Add 10 more for this month.",
};

export const labels = {
  recommendedBadge: "Most tables pick this",
  perMonth: "a month",
  free: "Free",
  comingSoon: "Coming soon",
  plansHeading: "Plans",
  morePlansHeading: "Bigger tables and extra hours",
  faqHeading: "Questions",
  swipeHint: "Swipe to see all three plans.",
  everyPlanHas:
    "Every plan has live transcripts, name memory, rules help, story memory and backups.",
  tryItHeading: "New here? Try it free.",
};

export interface Question {
  question: string;
  answer: string;
}

/** The plan rules, said plainly (#432). */
export const questions: readonly Question[] = [
  {
    question: "What counts as an hour?",
    answer:
      "The time DMbot is listening, from when you start it to when you stop it. Time when DMbot isn't listening doesn't count.",
  },
  {
    question: "Do unused hours carry over?",
    answer:
      "No. Your hours are for the month. Next month you get a full set again. If you run out, you can add 10 more hours.",
  },
  {
    question: "What's different between the plans?",
    answer:
      "Only the hours and how many campaigns you can run. Every plan has live transcripts, name memory, rules help, story memory and backups. The free Try It has everything except backups and downloads.",
  },
  {
    question: "Can I change my plan?",
    answer: "Yes, any time, from My account.",
  },
  {
    question: "What if I move to a plan with fewer campaigns?",
    answer:
      "The first campaigns you start after the change keep going, up to your new plan's limit. The others pause. Nothing in them is deleted. To play a paused one, pause another or change your plan.",
  },
  {
    question: "How long do you keep my campaign?",
    answer:
      "After your last game: 60 days on Try It, 6 months on Table, and 1 year on the other plans. If your plan stops being paid, we keep it for 120 days. We send you a message on Discord before anything is deleted.",
  },
  {
    question: "Can I give a campaign to someone else?",
    answer:
      "Yes. Use “Hand over this campaign” to give it to another person with a plan. It then uses their hours.",
  },
  {
    question: "Can I delete everything?",
    answer: "Yes. Ask from My account and we delete it all.",
  },
];

/** "$8.99" from 899. */
export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
