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
  showPassword: "Show password",
  hidePassword: "Hide password",
  signIn: "Sign in",
  signingIn: "Signing in…",
  // One text for a wrong email, a wrong password and a locked try, so it gives nothing away;
  // it names the pause and Google, so a locked owner isn't sent to doubt their password.
  wrong:
    "Not signed in. Check the email and password. After 5 wrong tries, sign-in pauses for 15 minutes, even with the right password: wait, or use Sign in with Google.",
  busy: "Too many sign-in tries right now. Try again in a minute.",
  googleFailed:
    "Google sign-in didn't work. Use the Google account with the admin email, or sign in with the password below. After 5 wrong tries, wait 15 minutes before trying again.",
  googleOff: "Google sign-in isn't set up on the server. Sign in with your email and password below.",
  // The full steps, not a short cut: ADMIN_EMAILS alone stops the website from starting.
  off: 'The admin page is off. To turn it on, follow "Turn on the admin page" in docs/DEPLOY.md on the server, then reload this page.',
  // Only the owner sees this page, so it can name where to look.
  down: "Can't reach DMbot right now. Try again in a minute. If you just changed .env, look at docker compose logs web-api on the server.",
  tryAgain: "Try again",
  signedInAs: (email: string): string => `Signed in as ${email}.`,
  comingSoon: "The free access list comes here next.",
  signOut: "Sign out",
  signOutFailed:
    "You're still signed in. Can't reach DMbot right now. Try Sign out again in a minute.",
  signedOut: "You're signed out.",
  // For part 3 (#773), when an admin change finds the session has ended.
  timedOut: "You're signed out: no use for an hour, or 12 hours in all. Sign in again.",
  needsScript: "This page needs JavaScript. Turn it on in your browser, then reload the page.",
} as const;
