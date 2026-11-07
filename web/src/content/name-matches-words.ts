// "DMbot's guesses" on the Names page (#3602), in plain words. Say "matched" and "guess", never "join" (the same
// words as Discord's card). The after-click lines are Discord's own.
export const matchWords = {
  heading: "DMbot's guesses",
  lead: "DMbot matched these names by itself. Only you know this for now. Players and recaps still treat each pair as two names until you say yes.",
  line: (spelling: string, keep: string): string => `DMbot guessed that ${spelling} is ${keep}.`,
  yes: { icon: "✅", text: "Yes, that's right" },
  undo: { icon: "↩️", text: "Undo" },
  all: (n: number): { icon: string; text: string } => ({ icon: "✅", text: `All ${n} are right` }),
  confirmed: (spelling: string, keep: string): string => `${spelling} is now ${keep}. Players can see it.`,
  undone: (spelling: string, keep: string): string => `Undone. ${spelling} is no longer ${keep}. It goes back to your list of names to check.`,
  confirmedMany: (n: number): string => `${n} guesses are now yours. Players can see them.`,
  stage: "Telling DMbot",
  unit: "guesses",
  loading: "Looking for DMbot's guesses…",
  failed: "DMbot's guesses didn't load. Reload the page to try again.",
  gone: "That guess is already answered, or was taken back. The list is up to date now.",
  other: "That didn't go through. Press the button again.",
  signedOut: "You were signed out. Sign in again, then press the button.",
  countOf: (n: number): string => (n === 1 ? "1 guess waits for you." : `${n} guesses wait for you.`),
} as const;
