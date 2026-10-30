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
  lead: "Show DMbot how your recaps should sound. Paste a recap you like. DMbot copies the style, not the story: nothing from your example goes into your recaps. Your campaign's game masters can see these examples, and they are kept in the campaign's backup, so leave out anything that must stay secret.",
  examplesHeading: "Example recaps",
  examplesIntro: "Recaps you like. DMbot copies how they sound.",
  usingOwn: (n: number): string => `${n} of ${STYLE_MAX_EXAMPLES} examples.`,
  usingDefaults: "No examples of your own yet, so DMbot is using its own.",
  useDefaults: "Delete mine, use DMbot's",
  deleteAsk: "This deletes your examples. Your notes stay. It can't be undone.",
  deleteYes: "Yes, delete mine",
  removeAsk: (n: number): string => `This deletes example ${n}. It can't be undone.`,
  removeYes: "Yes, delete it",
  keep: "Keep mine",
  remove: (n: number): string => `Remove example ${n}`,
  exampleLabel: "A recap you like",
  showAll: "Show all",
  exampleWords: (n: number): string => `About ${n.toLocaleString("en-US")} words`,
  exampleHelp: `Up to ${STYLE_EXAMPLE_WORDS} words. Only the sound is copied, and you can remove it any time.`,
  exampleFull: `You have ${STYLE_MAX_EXAMPLES} examples. Remove one to add another.`,
  addExample: "Add this example",
  count: (n: number): string => `${n.toLocaleString("en-US")} of ${STYLE_EXAMPLE_WORDS} words`,
  tooManyWords: (n: number): string => `That is ${n.toLocaleString("en-US")} words. The limit is ${STYLE_EXAMPLE_WORDS}. Cut about ${(n - STYLE_EXAMPLE_WORDS).toLocaleString("en-US")} words.`,
  notesHeading: "Notes",
  notesIntro: "Short instructions DMbot always follows, like \"Keep it short.\" or \"Write it like a bard telling it.\"",
  notesNone: "No notes yet.",
  noteLabel: "A note for DMbot",
  noteHelp: `Up to ${STYLE_NOTE_CHARS} characters. DMbot follows it in every recap.`,
  notesFull: `You have ${STYLE_MAX_NOTES} notes. Adding another removes the oldest.`,
  addNote: "Add this note",
  removeNote: (n: number): string => `Remove note ${n}`,
  sending: "Sending…",
  waiting: {
    add_example: "Adding your example. DMbot will do it in a moment.",
    remove_example: "Deleting that example. DMbot will do it in a moment.",
    use_defaults: "Deleting your examples. DMbot will do it in a moment.",
    add_note: "Adding your note. DMbot will do it in a moment.",
    remove_note: "Removing that note. DMbot will do it in a moment.",
  } as Record<string, string>,
  working: {
    add_example: "Adding your example now.",
    remove_example: "Deleting that example now.",
    use_defaults: "Deleting your examples now.",
    add_note: "Adding your note now.",
    remove_note: "Removing that note now.",
  } as Record<string, string>,
  doneNote: {
    add_example: "Added. Your next recap will use it.",
    remove_example: "Deleted. Your next recap won't use it.",
    use_defaults: "Deleted. Your next recap uses DMbot's own examples.",
    add_note: "Added. Your next recap will follow it.",
    remove_note: "Removed. Your next recap won't follow it.",
  } as Record<string, string>,
  problems: {
    "bad-choice": "That didn't go through. Check what you pasted and try again.",
    "bad-action": "That didn't go through. Try again.",
    "ticket-too-long": "That's too long. Keep it shorter.",
    full: "You already have three examples. Remove one first.",
    "too-soon": "DMbot is still catching up on your earlier changes. Wait a minute, then try again.",
    "not-allowed": "Only a campaign's game master can change the recap style. Ask this campaign's game master to change it.",
    "not-found": "We can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  failedReasons: {
    not_taken: "DMbot didn't get to it. Nothing was changed. Try again, or ask your server owner if it keeps happening.",
    full: `Nothing was changed. You already have ${STYLE_MAX_EXAMPLES} examples. Remove one first.`,
    gone: "Nothing was changed. That is already gone.",
    too_long: "Nothing was changed. That is too long. Keep it shorter.",
    empty: "Nothing was changed. That is empty.",
  } as Record<string, string>,
  failedFallback: "Nothing was changed. DMbot couldn't make the change. Try again.",
  genericProblem: "Something went wrong. Try again.",
};
