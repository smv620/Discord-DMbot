/**
 * Facts the legal pages share (#433), so the three drafts never disagree.
 * Placeholders in {{…}} are for the owner to fill; {{CHECK: …}} marks something to confirm.
 */
import { byId, formatPeriod, type PlanId } from "./pricing";
import facts from "./plans.json";

/**
 * The one place the publisher's name may appear on the site: a statement that we are not
 * connected to them. The trademark scan in test/pages.test.ts allows this file only.
 */
export const notAffiliated =
  "DMbot is not affiliated with or endorsed by Wizards of the Coast. It works with 5e-compatible tabletop games.";

export const operator = {
  name: "{{OWNER NAME}}",
  address: "{{OWNER ADDRESS}}",
  email: "{{CONTACT EMAIL}}",
};

export const paymentProvider =
  "{{PAYMENT PROVIDER: Paddle or Lemon Squeezy, once the owner chooses}}";

export const minimumAge = 13;

/** Graces and limits from #437 that the terms state. */
export const paymentGraceDays = 7;

/** Rows for the "how long we keep it" table, from the shared plan facts. */
export const retentionRows: { plan: string; keep: string }[] = (
  ["try-it", "table", "two-tables", "guild", "pro"] as PlanId[]
).map((id) => ({ plan: byId[id].name, keep: formatPeriod(byId[id].keepAfterLastSession) }));

export const keepAfterStopPaying = formatPeriod(
  facts.keepAfterPlanStopsPaying as { count: number; unit: "day" | "month" | "year" },
);

export const deletionWarningDays = facts.deletionWarningDaysBefore;
