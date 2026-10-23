// The words for "Names to check" on a recap's page (#2114 part 3; core #2113): the new names DMbot
// found in the recap and is not sure of. The DM answers each one; DMbot never decides what a name is.
// The answers are the same as Discord's 📝 Check new names, so answering in either place answers both.
// Shown only while RECAP_NAME_CHECKS_LIVE is true (recap-words.ts).
export const NOTE_MAX_LETTERS = 300; // core's limit for a note kept with a name (name_answers.NOTE_MAX)
export const SPELLING_MAX_LETTERS = 80;

export const nameCheckWords = {
  heading: "Names to check",
  lead: "DMbot isn't sure about these names. Tell it what each one is. Your recap stays as it is. You can also answer in Discord with 📝 Check new names.",
  yes: "Yes, it's right",
  kind: "What is it?",
  kindNone: "Pick what it is",
  // (the spoken name starts with the words on the button, so voice control finds it)
  yesWith: (name: string): string => `Yes, it's right: ${name}`,
  more: "Something else",
  actually: "It's the same as…",
  sameLabel: "A name you already have",
  sameNone: "Choose a name",
  sameUse: "Same name",
  sameFor: (name: string): string => `Same name: ${name}`,
  spellLabel: "Or spell it like this",
  spellUse: "Fix spelling",
  spellFor: (name: string): string => `Fix spelling: ${name}`,
  notAName: "Not a name",
  notANameFor: (name: string): string => `Not a name: ${name}`,
  explainLabel: "Say a few words about it",
  explainSend: "Send note",
  explainFor: (name: string): string => `Send note: ${name}`,
  left: (n: number): string => `${n} letters left`,
  noteFrom: "From the recap:",
  waiting: "DMbot is saving your answer…",
  slow: "Still saving. Your answer is kept, and the name will leave this list when it is done.",
  failedHint: "That didn't save. Press the button again.",
  done: "Saved.",
  allDone: "All the names are answered.",
  loadFailed: "We couldn't load the names to check. Nothing was changed. Open this page again in a minute.",
  problem: {
    "not-found": "That name is already answered. The list is up to date now.",
    "bad-text": "That text can't be saved. Use letters and numbers, and don't leave it empty.",
    "bad-target": "Choose a name from the list.",
    "bad-action": "That didn't work. Try again.",
    "too-soon": "Try again in a minute.",
    "not-allowed": "Only this campaign's DM can answer these names.",
    // What the bot writes when it could not apply an answer (core/.../recaps/name_answers.py), by its code
    // with the underscore as a hyphen. A test reads the codes from core so a new one needs words here.
    gone: "That name was already answered, here or in Discord. The list is up to date now.",
    "bad-kind": "That kind can't be used for a name. Pick another kind.",
    "not-joined": "Those two names can't be joined here. Choose another name, or open the Names page.",
    "bad-spelling": "That spelling can't be used. Try other letters.",
    failed: "That didn't save, and nothing was changed. Press the button again.",
    busy: "DMbot is busy with another answer. Try again in a moment.",
    network: "We couldn't reach DMbot, so nothing was changed. Check your connection and try again.",
    server: "Something went wrong. Try again in a minute.",
  } as Record<string, string>,
} as const;
