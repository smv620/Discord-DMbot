// The recap page's "which email" line (#2335). It says which address a recap email goes to, in the DM's
// own session only, and (for a payment email) how to change it in Stripe. It promises an email only
// when one will be sent: no address, or emails turned off, say so plainly instead. The account page's own
// email setting (#2349) is where the other addresses will be changed.
export const emailWords = {
  to: (address: string): string => `We'll email ${address} when it's ready.`,
  payment: "That's the email on your payment account.",
  changePayment: "Change your email in Stripe",
  paymentHelp: "In Stripe, open Billing information, then change your email.",
  opening: "Opening Stripe…",
  signin: "That's the email you signed in with.",
  none: "We don't have an email for you, so we won't send one. The recap will be on this page.",
  off: "Emails are off, so we won't email this recap. It will be on this page when it's ready.",
  turnOn: "Turn emails on",
  // The payment company's page can't be opened because payments are off: nothing else to point to
  // until the email setting on the account page exists (#2349).
  paymentsOff: "You can't change this email here yet.",
  failed: "We couldn't open Stripe. Try again in a minute.",
  signedOut: "You were signed out. Sign in, then try again.",
} as const;
