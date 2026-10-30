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
export const LORE_TEXT_MAX = 4000;
export const LORE_LINK_MAX = 2000;

export const lorePage = {
  heading: "Upload lore",
  back: "Back to the campaign",
  lead: "Give DMbot a backstory or notes about your world. It finds the names and how they connect, then asks you to check them. It never decides anything for you.",
  loading: "Loading…",
  signedOut: "Sign in to upload lore.",
  signIn: "Sign in with Discord",
  notOpen: "This page isn't open yet.",
  notDm: "Only a campaign's game master can upload lore.",
  whereLegend: "Where is it?",
  sources: { paste: "Paste it here", file: "A text file", link: "A link" },
  pasteLabel: "The words",
  pasteHelp: `Up to ${LORE_TEXT_MAX.toLocaleString("en-US")} characters. For more, upload it in parts.`,
  count: (n: number): string => `${n.toLocaleString("en-US")} of ${LORE_TEXT_MAX.toLocaleString("en-US")}`,
  fileLabel: "Choose a text file",
  fileHelp: `A plain text file (.txt or .md) of up to ${LORE_TEXT_MAX.toLocaleString("en-US")} characters. DMbot reads it in your browser and sends only the words.`,
  fileTooLong: `That file has more than ${LORE_TEXT_MAX.toLocaleString("en-US")} characters. Choose a shorter one, or paste the part you want.`,
  fileUnreadable: "DMbot couldn't read that file as text. Choose a plain text file, or paste the words.",
  fileChosen: (name: string): string => `Chosen: ${name}`,
  linkLabel: "The link",
  linkHelp: "A page anyone with the link can open, such as a shared Google Doc. It has to start with https://.",
  speakerLabel: "Whose backstory is this?",
  speakerNone: "Nobody in particular (about the world)",
  speakerHelp: "Pick a character if it is one character's story. You can leave this as it is.",
  speakerLoading: "Loading the characters…",
  knownLegend: "Who knows it?",
  known: {
    world: { label: "Everyone in the story", note: "General knowledge of the world." },
    spoilers: { label: "Only me", note: "A secret. Only you, the game master, see it until you say otherwise." },
    smart: { label: "Let DMbot guess", note: "You check each one before it is kept." },
  },
  rights: "I have the right to use this text. DMbot reads it for this campaign only and keeps the names and connections it finds, not the text.",
  rightsNeeded: "Tick the box to say you have the right to use this text.",
  submit: "Upload lore",
  sending: "Sending…",
  nothingYet: "Add some words, a file or a link first.",
  // Said by the stage code the API sends.
  stages: {
    waiting: "Waiting for DMbot to pick it up.",
    reading: "Reading it.",
    finding_names: "Finding names.",
    finding_links: "Finding how they connect.",
    done: "Done.",
  } as Record<string, string>,
  unit: "parts",
  found: (names: number, links: number): string =>
    `DMbot found ${names} ${names === 1 ? "name" : "names"} and ${links} ${links === 1 ? "connection" : "connections"}.`,
  nextLead: "Check what it found:",
  checkNames: "Check new names",
  seeGraph: "See the Story Graph",
  again: "Upload more lore",
  openNote: "You already have lore being read. Here is where it is.",
  problems: {
    "needs-confirm": "Tick the box to say you have the right to use this text.",
    "too-soon": "That's five uploads in the last hour. Try again a little later.",
    "bad-text": "DMbot couldn't use those words or that link. Check them and try again.",
    "ticket-too-long": `That's too long. The most is ${LORE_TEXT_MAX.toLocaleString("en-US")} characters.`,
    "bad-speaker": "DMbot doesn't know that character. Pick another, or choose Nobody in particular.",
    "not-allowed": "Only a campaign's game master can upload lore.",
    "not-found": "This page isn't open yet.",
  } as Record<string, string>,
  failedReasons: {
    not_taken: "DMbot didn't pick it up in time.",
  } as Record<string, string>,
  failedFallback: "DMbot couldn't read it.",
  genericProblem: "Something went wrong. Try again.",
};
