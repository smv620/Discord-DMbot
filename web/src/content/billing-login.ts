// Stripe's billing login link (#1711): someone who has lost their Discord account can still reach
// billing (cancel, update the card, get invoices) with the email on their receipts. The link comes
// from the build (PUBLIC_STRIPE_PORTAL_LOGIN_URL) because test and live mode have different links;
// it is not a secret, but it is never written in the repository.
import type { Step } from "./help";

const HOST = "billing.stripe.com";

/** The address to show, or null. Only an https address on billing.stripe.com counts, so a typo in
 * the setting can't send people anywhere else. Read when called, so a test can change it. */
export function portalLoginUrl(raw: string | undefined = import.meta.env.PUBLIC_STRIPE_PORTAL_LOGIN_URL): string | null {
  const value = (raw ?? "").trim();
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== HOST || url.username || url.password || url.port) return null;
    return url.href;
  } catch {
    return null;
  }
}

export const portalWords = {
  /** Refunds, "Stopping your plan". */
  refunds: "Can't sign in with Discord? Manage your plan with the email on your receipts:",
  /** The link text on Refunds and in Help. */
  linkText: "Manage your plan with your email",
  help: "Can't sign in with Discord? Use the email on your receipts. Stripe, who handle our payments, will send you a code. There you can cancel, update your card or get invoices.",
  /** My Account, next to the plan buttons. */
  account: "No Discord? Use your email",
};

/** The Help step for a lost Discord account; nothing while the setting is empty or not Stripe's. */
export function portalLoginSteps(url: string | null): Step[] {
  if (!url) return [];
  return [{ text: portalWords.help, link: { text: portalWords.linkText, href: url } }];
}
