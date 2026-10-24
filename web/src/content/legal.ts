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

/**
 * Who runs DMbot (owner, 2026-10-10, #922). There is no address on purpose: the owner chose
 * email-only contact, and a postal address never goes on this public site or in this repo.
 */
export const operator = {
  name: "DMbot LLC",
  email: "dmbot.admin@gmail.com",
};

/** The payment company, chosen by the owner (2026-10-13, #1096). It is the seller (merchant of record). */
export const paymentProvider = "Stripe";

/**
 * The training sentence (#1328, #1329) as the Anthropic line in privacy.astro says it. The public
 * Q&A section (QabotPrivacy.astro) uses this constant, and qabot-privacy.test.ts checks that the
 * rendered Anthropic line still holds it word for word, so the two can never drift apart. The
 * yes message says the same in its own words (core/src/dmbot/consent_dm.py; legal.test.ts).
 */
export const aiTrainingSentence = "That text isn't used to train Anthropic's AI.";

/**
 * Google (Gemini) as the recap writer, only if whoever runs DMbot switches it on (#2575, code #2582; the owner's
 * sign-off is on #2574). The page cannot tell whether it is on, so it says "only if switched on". Owner decision,
 * 2026-10-23 (#2590, PLAN "Two models"): the Gemini writer reads the session's whole cleaned transcript (what was
 * said by people who agreed to be recorded, off-topic chat left out) with PlotBot's guide to what matters, so the
 * line says that, plus the list of what happened, the names, the last recap, the DM's style notes and fixes. It
 * never gets sound, the as-heard (raw) transcript or anything from the DM screen. Only the recap-writing step
 * moves to Gemini (Feature.RECAP_WRITE on the CREATIVE tier, #2582); the beats and every check stay with Anthropic.
 * "Not used to train" is true under Google's paid terms only, so the line says so and links them.
 */
export const geminiPrivacy = {
  name: "Google (Gemini)",
  lead: "only if switched on.",
  body: "DMbot can have a recap written by Google's AI instead of Anthropic's. It is off unless DMbot LLC turns it on. If it is on, Google gets the session's cleaned transcript (what was said at the table by people who agreed to be recorded, with the off-topic chat left out), a short list of what happened (and who and where), the names DMbot knows, the last recap, the DM's notes on how recaps should sound, and any fixes the DM asked for. It never gets sound, the as-heard transcript, or anything from the DM screen. Google's paid terms say it doesn't use that text to train its AI.",
  country: "Google is a company in the United States.",
  termsText: "Google's terms for paid use",
  termsUrl: "https://ai.google.dev/gemini-api/terms",
};

/** The day the owner signed the legal pages off (2026-10-09); shown on all three. */
export const effectiveDate = "9 October 2026";

/** The minimum age to use DMbot (owner, 2026-10-10): 16, on the terms and privacy pages. */
export const minimumAge = 16;

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
