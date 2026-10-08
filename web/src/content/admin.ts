/**
 * Every word on the admin page (#772). Only the owner sees it, but it stays plain and
 * says what to do next, like the rest of the site.
 */
export const text = {
  title: "Admin",
  description: "DMbot's admin page, for the team only.",
  heading: "Admin",
  loading: "Checking if you're signed in…",
  signInLead: "Sign in to manage free access.",
  google: "Sign in with Google",
  or: "Or use your admin email and password:",
  email: "Email",
  password: "Password",
  signIn: "Sign in",
  signingIn: "Signing in…",
  wrong:
    "That didn't work. Check the email and password. After 5 wrong tries, wait 15 minutes before trying again.",
  googleFailed:
    "Google sign-in didn't work. Use the Google account with the admin email, or sign in with the password below. After 5 wrong tries, wait 15 minutes before trying again.",
  googleOff: "Google sign-in isn't set up on the server. Sign in with your email and password below.",
  off: "The admin page is switched off. Put your email in ADMIN_EMAILS on the server, restart the website API, then reload this page.",
  down: "Can't reach DMbot right now. Try again in a minute.",
  tryAgain: "Try again",
  signedInAs: (email: string): string => `Signed in as ${email}.`,
  comingSoon: "The free access list comes here next.",
  signOut: "Sign out",
  signOutFailed:
    "You're still signed in. Can't reach DMbot right now. Try Sign out again in a minute.",
  signedOut: "You're signed out.",
  needsScript: "This page needs JavaScript. Turn it on in your browser, then reload the page.",
} as const;
