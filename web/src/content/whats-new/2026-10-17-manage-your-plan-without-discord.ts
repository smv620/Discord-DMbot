import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-17",
  title: "Manage your plan without Discord",
  what: "If you can't sign in with Discord, use the email on your receipts. Stripe sends you a code, and you can cancel, update your card or get invoices.",
  held: "Held until the billing login link is set in the website's build (PUBLIC_STRIPE_PORTAL_LOGIN_URL, #1711); move it to the news when the link shows.",
  order: 21,
};
