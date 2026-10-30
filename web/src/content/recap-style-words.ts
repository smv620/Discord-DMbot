// The words about recap style on the website (#2985): the example recaps and standing notes that show how a campaign's
// recaps sound. The same as Discord's ✍️ Recap style panel: up to 3 examples of up to 600 words, up to 10 standing notes
// of up to 300 characters, and DMbot's own examples when a campaign has none. DMbot copies how an example sounds, never
// what happens in it. DM only: the examples are the DM's own pasted recaps.
//
// HOW IT TURNS ON: when migration 0290 and CloudDev's bot step that applies the queued changes are deployed and dev1 has
// added an example on the website and seen it in Discord, the What's new entry "Choose how your recaps sound" moves out of
// `held` and RECAP_STYLE_LIVE becomes true in the same change; DMBOT_WEB_RECAP_STYLE=1 is set after the bot (GO_LIVE.md).
export const RECAP_STYLE_LIVE = false;

/** Mirror the limits of Discord's panel and of the door (core web/recap_style.py): change them together. */
export const STYLE_MAX_EXAMPLES = 3;
export const STYLE_MAX_NOTES = 10;
export const STYLE_EXAMPLE_WORDS = 600;
export const STYLE_NOTE_CHARS = 300;

export const recapStylePage = {
  heading: "Recap style",
  back: "Back to the campaign",
  loading: "Loading…",
  notOpen: "This page isn't open yet.",
  notFound: "We can't find that campaign. Go back to your campaigns and choose it.",
  lead: "Show DMbot how your recaps should sound. Paste a recap you like. DMbot copies how it sounds, never what happens in it.",
  examplesHeading: "Your examples",
  usingOwn: (n: number): string => `${n} of ${STYLE_MAX_EXAMPLES} examples.`,
  usingDefaults: "No examples of your own yet, so DMbot is using its own.",
  useDefaults: "Use DMbot's own examples",
  useDefaultsNote: "Takes away all your examples.",
  remove: (n: number): string => `Remove example ${n}`,
  exampleLabel: "A recap you like",
  exampleHelp: `Up to ${STYLE_EXAMPLE_WORDS} words. Only the sound is copied, and you can remove it any time.`,
  exampleFull: `You have ${STYLE_MAX_EXAMPLES} examples. Remove one to add another.`,
  addExample: "Add this example",
  count: (n: number): string => `${n.toLocaleString("en-US")} of ${STYLE_EXAMPLE_WORDS} words`,
  tooManyWords: `That is more than ${STYLE_EXAMPLE_WORDS} words. Keep it shorter.`,
  notesHeading: "Notes for every recap",
  notesNone: "No notes yet.",
  noteLabel: "A note, one short sentence",
  noteHelp: `Up to ${STYLE_NOTE_CHARS} characters, for example "Keep it short." DMbot follows these for every recap.`,
  notesFull: `You have ${STYLE_MAX_NOTES} notes. Add a new one and the oldest goes.`,
  addNote: "Add this note",
  removeNote: (n: number): string => `Remove note ${n}`,
  sending: "Sending…",
  waiting: "Waiting for DMbot to make the change.",
  doneNote: "Done. DMbot made the change.",
  working: "DMbot is making the change.",
  problems: {
    "bad-choice": "DMbot couldn't use that. Check it and try again.",
    "bad-action": "DMbot couldn't use that. Try again.",
    "ticket-too-long": "That's too long. Keep it shorter.",
    "too-soon": "There are already ten changes waiting. Try again in a minute.",
    "not-allowed": "Only a campaign's game master can change the recap style.",
    "not-found": "This page isn't open yet.",
  } as Record<string, string>,
  failedReasons: {
    not_taken: "DMbot didn't pick it up in time. Try again in a few minutes.",
    full: `You already have ${STYLE_MAX_EXAMPLES} examples. Remove one first.`,
    gone: "That is already gone.",
    too_long: "That is too long. Keep it shorter.",
    empty: "That is empty.",
  } as Record<string, string>,
  failedFallback: "DMbot couldn't make the change. Try again.",
  genericProblem: "Something went wrong. Try again.",
};
