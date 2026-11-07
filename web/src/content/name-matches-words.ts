// "DMbot's guesses" on the Names page (#3602), in plain words. Say "matched" and "guess", never "join" (the same
// words as Discord's card). The after-click lines are Discord's own.
export const matchWords = {
  heading: "DMbot's guesses",
  lead: "DMbot matched these names. Until you say yes, players and recaps still see two names. Undo takes a guess back.",
  checkFirst: (n: number): string => `Check the list first. This says yes to all ${n}.`,
  line: (spelling: string, keep: string): string => `DMbot guessed that ${spelling} is ${keep}.`,
  yes: { icon: "✅", text: "Yes, that's right" },
  undo: { icon: "↩️", text: "Undo" },
  all: (n: number): { icon: string; text: string } => ({ icon: "✅", text: `All ${n} are right` }),
  confirmed: (spelling: string, keep: string): string => `${spelling} is now ${keep}. Players can see it.`,
  undone: (spelling: string, keep: string): string => `Undone. ${spelling} is no longer ${keep}. It goes back to your list of names to check.`,
  confirmedMany: (n: number): string => (n === 1 ? "1 guess is now yours. Players can see it." : `${n} guesses are now yours. Players can see them.`),
  someDone: (ok: number, n: number): string => `${ok} of ${n} are done. ${n - ok} still wait for you.`,
  stage: "Saving your answers",
  unit: "answers",
  loading: "Loading DMbot's guesses…",
  failed: "DMbot's guesses didn't load. Reload the page to try again.",
  gone: "Someone already answered that guess. The list is up to date now.",
  other: "That didn't go through. The guess is still waiting. Press the button again.",
  signedOut: "You were signed out. Sign in again, then press the button.",
  // On the Questions page (#3701): the count of the same list, and where to answer it.
  checkThem: "Check DMbot's guesses",
  countOf: (n: number): string => (n === 1 ? "1 guess waits for you." : `${n} guesses wait for you.`),
} as const;
