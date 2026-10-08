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
  // it names the pause (and Google, when it's set up), so a locked owner isn't sent to
  // doubt their password.
  wrong:
    "Not signed in. Check the email and password. After 5 wrong tries, sign-in pauses for 15 minutes, even with the right password. If the wrong tries weren't yours, \"Sign in with Google\" still works.",
  wrongNoGoogle:
    "Not signed in. Check the email and password. After 5 wrong tries, sign-in pauses for 15 minutes, even with the right password.",
  busy: "Too many sign-in tries right now. Try again in a minute.",
  googleFailed:
    "Google sign-in didn't work. Use the Google account with the admin email, or sign in with the password below. After 5 wrong tries, wait 15 minutes before trying again.",
  googleFailedNoPassword:
    "Google sign-in didn't work. Use the Google account with the admin email. After 5 wrong tries, wait 15 minutes before trying again.",
  googleOff: "Google sign-in isn't set up on the server. Sign in with your email and password below.",
  // The full steps, not a short cut: ADMIN_EMAILS alone stops the website from starting.
  off: 'The admin page is off. To turn it on, follow "Turn on the admin page" in docs/DEPLOY.md, in DMbot\'s GitHub or on the server, then reload this page.',
  // Only the owner sees this page; the server is dev1's to look at (CLAUDE.md).
  down: "Can't reach DMbot right now. Try again in a minute. If it keeps happening, tell dev1.",
  tryAgain: "Try again",
  signedInAs: (email: string): string => `Signed in as ${email}.`,
  signOut: "Sign out",
  signOutFailed:
    "You're still signed in. Can't reach DMbot right now. Try \"Sign out\" again in a minute.",
  signedOut: "You're signed out.",
  // For part 3 (#773), when an admin change finds the session has ended.
  timedOut:
    "You're signed out. That happens after an hour without use, or 12 hours after you signed in. Sign in again.",
  needsScript: "This page needs JavaScript. Turn it on in your browser, then reload the page.",

  // Free access (#773). "Discord user id" is the one technical term: it's what Discord
  // calls the thing to copy.
  freeHeading: "Free access",
  freeLead: "These people use DMbot without paying.",
  listLoading: "Loading the list…",
  alwaysFree: "Always free (set on the server)",
  levels: { guild: "Like Guild", unlimited: "No limits" } as Record<string, string>,
  noEnd: "No end date",
  until: (date: string): string => `Until ${date}`,
  // granted_by and granted_at are rewritten by a change, so "set", not "added".
  setBy: (by: string, date: string): string => `Set by ${by} on ${date}`,
  ended: (date: string): string => `Ended ${date}`,
  change: "Change",
  nobody: "Nobody has free access from this page yet. Add someone below.",
  revoke: "Revoke",
  confirmRevoke: (id: string): string => `Revoke free access for ${id}?`,
  yesRevoke: "Yes, revoke",
  cancel: "Cancel",
  revoked: (id: string): string => `Done. ${id} no longer has free access.`,
  addHeading: "Add someone",
  addLead: "To change someone's free access, add them again with the new details, or tap Change.",
  idLabel: "Discord user id",
  idHint:
    "In Discord: Settings, then Advanced, then turn on Developer Mode. Then right-click the person and pick Copy User ID.",
  levelLabel: "What they get",
  levelHint: "Like Guild: the same as paying for Guild. No limits: no caps at all.",
  endLabel: "Last day (optional)",
  // Whole days in UTC, as the API stores them.
  endHint:
    "Free access works through this day (UTC time). Leave it empty so it never ends.",
  noteLabel: "Note (optional)",
  noteHint: "Who this is, or why. Only admins see this. 200 letters at most.",
  add: "Add",
  adding: "Adding…",
  added: (id: string): string => `Done. ${id} has free access.`,
  changed: (id: string): string => `Done. ${id}'s free access is changed.`,
  historyHeading: "Recent changes",
  noHistory: "No changes yet.",
  logLine: (when: string, by: string, action: string, id: string): string =>
    `${when}: ${by} ${
      action === "grant"
        ? "gave free access to"
        : action === "change"
          ? "changed free access for"
          : "revoked free access for"
    } ${id}`,
  grantErrors: {
    "bad-id":
      "That isn't a Discord user id: it's 17 to 20 digits. Copy it again in Discord and paste it here.",
    "bad-level": "Pick Like Guild or No limits.",
    "no-id": "Paste the person's Discord user id first.",
    "bad-date": "That end date didn't work. Pick it from the calendar, or leave it empty.",
    "past-date": "Pick an end date after today, or leave it empty so it never ends.",
    "long-note": "Keep the note to 200 letters or fewer.",
    "bad-note": "The note has a character DMbot can't keep. Type it again with plain letters.",
    "already-free":
      "That person is always free (set on the server), so there's nothing to add here.",
    stale: "That didn't work. Reload this page and try again.",
    "no-grant": "That person has no free access to revoke. We've updated the list.",
  } as Record<string, string>,
} as const;
