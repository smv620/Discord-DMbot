// The words about uploading lore (#3022; docs/PLAN.md, "Upload lore"; #2985 step 2). A DM pastes a backstory or notes about
// the world, gives a text file or a link, says whose it is and how it is known, and confirms the right to use it. DMbot reads
// it the same way as Discord's upload and offers the names and connections it finds to be checked: nothing becomes a fact on
// its own, and the words themselves are not kept.
//
// HOW IT TURNS ON: when the web door (migration 0288) and CloudDev's bot step (#3022) are deployed and dev1 has uploaded a
// short text for a test campaign and seen the names arrive, the What's new entry "Upload lore" moves out of `held`
// and LORE_LIVE becomes true in the same change; the server switch DMBOT_WEB_LORE=1 is set after the bot (docs/GO_LIVE.md).
export const LORE_LIVE = false;

/** Mirror the core limits (web/lore.py, the same as Discord's form): change them together. */
export const LORE_TEXT_MAX = 262_144;
export const LORE_LINK_MAX = 2000;

export const lorePage = {
  heading: "Upload lore",
  back: "Back to the campaign",
  lead: "Give DMbot a backstory or notes about your world. It finds the names and how they connect, then you check them. You decide what stays.",
  loading: "Loading…",
  signedOut: "Sign in to upload lore.",
  signIn: "Sign in with Discord",
  notOpen: "This page isn't open yet.",
  notFound: "We can't find that campaign. Go back to your campaigns and choose it.",
  notDm: "Only a campaign's game master can upload lore.",
  whereLegend: "How do you want to add it?",
  sources: { paste: "Paste it here", file: "A text file", link: "A link" },
  pasteLabel: "The words",
  pasteHelp: `Up to ${LORE_TEXT_MAX.toLocaleString("en-US")} characters.`,
  count: (n: number): string => `${n.toLocaleString("en-US")} of ${LORE_TEXT_MAX.toLocaleString("en-US")}`,
  fileLabel: "Choose a text file",
  fileHelp: `Choose a plain text file (.txt or .md) of up to ${LORE_TEXT_MAX.toLocaleString("en-US")} characters. Only the words are sent.`,
  fileTooLong: `That file has more than ${LORE_TEXT_MAX.toLocaleString("en-US")} characters. Choose a shorter one, or paste the part you want.`,
  fileUnreadable: "DMbot couldn't read that file as text. Choose a plain text file, or paste the words.",
  fileChosen: (name: string): string => `Chosen: ${name}`,
  linkLabel: "The link",
  linkHelp: "A page anyone with the link can open, such as a shared Google Doc. Paste the full address.",
  speakerLabel: "Whose story is this?",
  speakerNone: "Nobody in particular (about the world)",
  speakerHelp: "Pick a character if this is one character's backstory. It does not change who knows it; the next question does.",
  speakerLoading: "Loading the characters…",
  knownLegend: "Who knows it?",
  known: {
    world: { label: "Everyone in the story", note: "Every player's character knows all of it. Good for common knowledge about the world." },
    spoilers: { label: "Only me (the DM)", note: "A secret. Players won't see any of it on the Story Graph." },
    smart: { label: "Let DMbot guess", note: "DMbot guesses for each fact: everyone knows it, or only you. It can be wrong. Fix any later with \"Who knows this?\" on the Story Graph." },
  },
  rights: "I have the right to use this text.",
  rightsNote: "DMbot doesn't check this for you. It uses the text for this campaign only, and keeps the names and connections it finds, not your text.",
  rightsNeeded: "Check the box to confirm you have the right to use this text.",
  submit: "Upload lore",
  sending: "Sending…",
  nothingYet: "Add some words, a file or a link first.",
  // Said by the stage code the API sends.
  stages: {
    waiting: "Waiting for DMbot to pick it up.",
    reading: "Reading it.",
    finding_names: "Finding names.",
    finding_links: "Finding how they connect.",
    sorting: "Working out what the players know.",
    done: "Done.",
  } as Record<string, string>,
  unit: "parts",
  found: (names: number, links: number): string =>
    `DMbot found ${names} ${names === 1 ? "name" : "names"} and ${links} ${links === 1 ? "connection" : "connections"}.`,
  foundNothing: "DMbot didn't find any names in that. Try a longer piece.",
  nextLead: "Nothing is kept until you check it:",
  checkNames: "Check the new names",
  seeGraph: "See the Story Graph",
  guessed: "DMbot guessed who knows each one. Change any on the Story Graph with \"Who knows this?\"",
  again: "Upload more lore",
  retry: "Try again",
  openNote: "You already have lore being read. Here is where it is.",
  problems: {
    "needs-confirm": "Check the box to confirm you have the right to use this text.",
    "too-soon": "That's five uploads in the last hour. You can try again soon.",
    "bad-text": "DMbot couldn't use those words or that link. Check them and try again.",
    "ticket-too-long": `That's too long. The most is ${LORE_TEXT_MAX.toLocaleString("en-US")} characters.`,
    "bad-speaker": "DMbot doesn't know that character. Pick another, or choose Nobody in particular.",
    "not-allowed": "Only a campaign's game master can upload lore.",
    "not-found": "We can't find that campaign. Go back to your campaigns and choose it.",
  } as Record<string, string>,
  failedReasons: {
    not_taken: "DMbot didn't pick it up in time. Try again in a few minutes.",
  } as Record<string, string>,
  failedFallback: "DMbot couldn't read it. Check the text or link, then try again.",
  genericProblem: "Something went wrong. Try again.",
};
