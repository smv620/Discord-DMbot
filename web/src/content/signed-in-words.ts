// The line at the top of every signed-in page (#2301): who is signed in, or a way to sign in.
// Only the display name is ever shown, never an email or a Discord number.
export const signedInLine = {
  as: "Signed in as",
  notYou: "Not you?",
  signOut: "Sign out",
  signingOut: "Signing you out…",
  notSignedIn: "You're not signed in.",
  signIn: "Sign in with Discord",
  // Said by a screen reader when the line is still asking who is signed in.
  checking: "Checking who is signed in…",
  couldNotSignOut: "Couldn't sign you out. Press Sign out to try again.",
} as const;
