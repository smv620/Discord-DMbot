// The recap page's "which email, and how to change it" line (#2335). It says which address a recap
// email goes to, in the DM's own session only, and where to change it. It promises an email only
// when one will be sent: no address, or emails turned off, say so plainly instead.
export const emailWords = {
  to: (address: string): string => `We'll email ${address} when it's ready.`,
  payment: "That's the email on your payment account.",
  changePayment: "Change your email in Stripe",
  paymentHelp: "In Stripe, open Billing information, then change your email.",
  opening: "Opening Stripe…",
  signin: "That's the email you signed in with.",
  change: "Change your email",
  none: "We don't have your email, so we can't email this recap. It will be on this page.",
  add: "Add an email on your account page",
  off: "Emails are off, so we won't email this recap. It will be on this page when it's ready.",
  turnOn: "Turn emails on",
  // The payment company isn't switched on yet: the page still says where the email can be changed.
  paymentsOff: "You can change your email on your account page.",
  accountPage: "Open your account page",
  failed: "We couldn't open Stripe. Try again in a minute.",
  signedOut: "You were signed out. Sign in, then try again.",
} as const;
