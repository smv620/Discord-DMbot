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
  // calls the thing to copy. The error texts are the agreed #804 words (grants.py).
  freeHeading: "Free access",
  freeLead:
    "These people use DMbot without paying. People marked “Always free” are set on the server. Ask dev1 to change those.",
  listLoading: "Loading the list…",
  alwaysFree: "Always free (set on the server)",
  levels: { guild: "Same as Guild", unlimited: "No limits" } as Record<string, string>,
  noEnd: "No end date",
  until: (date: string): string => `Until ${date}`,
  // granted_by and granted_at are rewritten by a change, so "set", not "added".
  setBy: (by: string, date: string): string => `Set by ${by} on ${date}`,
  ended: (date: string): string => `Ended ${date}. Tap Change to give it again.`,
  change: "Change",
  nobody: "Nobody has free access from this page yet. Add someone below.",
  revoke: "Revoke",
  // `who` is the person's name, or a shortened id when they haven't signed in lately.
  confirmRevoke: (who: string): string => `Revoke free access for ${who}?`,
  yesRevoke: "Yes, revoke",
  cancel: "Cancel",
  revoked: (who: string): string => `Done. ${who} no longer has free access.`,
  addHeading: "Add someone",
  addLead: "To change someone's free access, tap Change in the list.",
  changeHeading: (who: string): string => `Change free access for ${who}`,
  idLabel: "Discord user id",
  idHint:
    "First turn on Developer Mode in Discord: Settings, then Advanced. Then on a computer, right-click the person. On a phone, tap their name, then the three dots. Pick Copy User ID.",
  levelLabel: "How much",
  levelHint: "Same as Guild: the same limits as the Guild plan. No limits: no caps at all.",
  endLabel: "Last day (optional)",
  endHint: "They keep free access until the end of this day. Leave it empty so it never ends.",
  noEndButton: "No end date",
  noteLabel: "Note (optional)",
  noteHint: (max: number): string =>
    `Who this is, or why. Only admins see this. ${max} letters at most.`,
  add: "Give free access",
  save: "Save changes",
  startOver: "Start over",
  adding: "Saving…",
  added: (who: string): string => `Done. ${who} has free access.`,
  changed: (who: string): string => `Done. ${who}'s free access is changed.`,
  historyHeading: "Recent changes",
  noHistory: "No changes yet.",
  logLine: (when: string, by: string, action: string, who: string): string =>
    `${when}: ${by} ${
      action === "grant"
        ? "gave free access to"
        : action === "change"
          ? "changed free access for"
          : "revoked free access for"
    } ${who}`,
  grantErrors: {
    "bad-id":
      "That isn't a Discord account number. In Discord, right-click the person and pick Copy User ID.",
    "bad-level": "Pick how much: Same as Guild, or No limits.",
    "no-id": "Paste the person's Discord user id first.",
    "bad-date": "That end date didn't work. Pick it from the calendar, or leave it empty.",
    "past-date": "Pick an end date after today, or leave it empty so it never ends.",
    "long-note": (max: number): string => `Keep the note to ${max} characters or fewer.`,
    "bad-note": "The note has a character DMbot can't keep. Type it again with plain letters.",
    "already-free":
      "That person is always free (set on the server), so there's nothing to add here.",
    stale: "That didn't work. Reload this page and try again.",
    "no-grant": "That person has no free access to revoke. We've updated the list.",
  } as Record<string, string | ((max: number) => string)>,
} as const;

/** The longest note, in characters: the same number the API and the database use. */
export const NOTE_MAX = 200;

/** A grant's note-length refusal and the others as one text. */
export function grantError(kind: string): string | undefined {
  const words = text.grantErrors[kind];
  return typeof words === "function" ? words(NOTE_MAX) : words;
}

/** Who a row is about: their name, or a shortened id ("1234…5678") if they haven't
 * signed in lately. The full id stays visible on the list; messages stay short. */
export function who(name: string | null, discordId: string): string {
  if (name) return name;
  return discordId.length > 10 ? `${discordId.slice(0, 4)}…${discordId.slice(-4)}` : discordId;
}
