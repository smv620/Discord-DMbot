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

/** The payment company, chosen by the owner (2026-10-09). It is the seller (merchant of record). */
export const paymentProvider = "Lemon Squeezy";

export const minimumAge = 13;

/** Days to fix a failed payment before the plan stops (#437). */
export const paymentGraceDays = facts.paymentGraceDays;

/** Rows for the "how long we keep it" table, from the shared plan facts. */
export const retentionRows: { plan: string; keep: string }[] = (facts.order as PlanId[]).map(
  (id) => ({ plan: byId[id].name, keep: formatPeriod(byId[id].keepAfterLastSession) }),
);

export const keepAfterStopPaying = formatPeriod(
  facts.keepAfterPlanStopsPaying as { count: number; unit: "day" | "month" | "year" },
);

/** "14 days and 3 days", from however many warnings the plan file lists. */
export const deletionWarnings = ((days: number[]): string => {
  const parts = days.map((d) => `${d} day${d === 1 ? "" : "s"}`);
  return parts.length <= 1
    ? (parts[0] ?? "")
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
})(facts.deletionWarningDaysBefore);
