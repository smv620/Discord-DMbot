// "Before you start" on the home page (#1324): what a game master wants to know before bringing
// DMbot to a table. Each answer is one to three plain sentences and true of what is built; the
// source is in the comment above it. No privacy wording is written here: an answer that needs
// more links to the privacy page (CLAUDE.md, "Simple enough for a child"; no legal promises
// beyond what that page says). No prices: the last answer links to the Prices page instead.
export interface Question {
  question: string;
  answer: string;
  /** A link that finishes the answer, to a page the site already has. */
  more?: { text: string; href: string };
}

export const beforeHeading = "Before you start";

export const beforeQuestions: readonly Question[] = [
  {
    // CLAUDE.md "Consent"; ears' allowlist and its bot check (ears/src/voiceMembers.ts); the
    // yes message (consent_dm.py) says 16 or older and that typed text counts too; the Stop button
    // is on its private message, and what was already written stays ("It stays there even if you
    // stop later"). Stop asks once more to confirm (dm_screen/messages.py).
    question: "Does it record everyone?",
    answer:
      "No. Only people who press yes (16 or older) are recorded, for what they say and type, and bots never are. Anyone can stop at any time with 🗂️ Menu, then Stop recording me; what was already written stays in the record.",
  },
  {
    // CLAUDE.md "Transcripts are shared; the DM screen is not pushed to players"; the three
    // choices are the buttons in dm_screen/messages.py (VISIBILITY_BUTTONS, WHO_CAN_SEE; "Your DM
    // will see that you peeked" is in PEEK_WARNING; "Server owners and admins can always see every
    // channel" is in that file too), "peek" is the default (schema.py,
    // campaigns.dm_screen_visibility), and a paid campaign's backup holds the DM's notes
    // (CLAUDE.md, "Backups").
    question: "Can my players see my DM notes?",
    answer:
      "You choose. Only you; players can peek after a spoiler warning (the usual setting, and you can see who peeked); or everyone in the server. Server owners and admins can always see every channel, and a paid campaign's backup holds your notes too.",
  },
  {
    // CLAUDE.md "DM authority" and "Citations": tips go only to the DM screen, with a source.
    question: "Will DMbot take over my game?",
    answer:
      "No. It sends you quiet tips with where they come from, and you decide. It never makes up story.",
  },
  {
    // CLAUDE.md "Transcripts are shared"; the words the yes message uses (consent_dm.py: "Anyone
    // in this server can read and download that text. It stays there even if you stop later").
    // It does not say only people who said yes are in it: the DM sidebar's and table questions
    // go in the raw record too. The privacy page has the rest.
    question: "Who can read the game's written record?",
    answer:
      "Everyone in your Discord server can read and download it. The message that asks players to say yes tells them so, and says it stays there even if they stop later.",
    more: { text: "Read the privacy page", href: "/legal/privacy" },
  },
  {
    // Only what is already said to people: the yes message (consent_dm.py: AI_NOTE, AI_SHORT and
    // outside_note: an outside company writes the voice down, an AI company, Anthropic, reads the
    // text) and the privacy page ("Companies that help us": Deepgram turns the sound into text,
    // Anthropic is the AI that helps with names, rules and story notes and gets text, not sound;
    // "the sound is thrown away", and, since #1329, "That text isn't used to train Anthropic's
    // AI", the same claim the yes message makes). A test checks every company named here is on
    // the privacy page and that the training sentence is the privacy page's own words. No new
    // privacy wording: it repeats that sentence and links there.
    question: "Does it send what we say to an AI?",
    answer:
      "Yes, in two steps: a speech company (Deepgram) turns the sound of people who said yes into text, and the sound is thrown away. An AI company (Anthropic) then gets the text it needs, not sound, to help with names, rules and story notes. That text isn't used to train Anthropic's AI.",
    more: { text: "Read the privacy page", href: "/legal/privacy" },
  },
  {
    // CLAUDE.md "Rules edition" (newest first, older tagged) and the order in rules/index.py:
    // house rules sit above everything else.
    question: "Which rules does it use?",
    answer:
      "The newest rules first, and older ones are marked Legacy. Your own house rules come first.",
  },
  {
    // The Help page's first steps (web/src/content/help.ts) and the install page. No plan or
    // price is repeated here: the link goes to the Prices page.
    question: "What do I need?",
    answer:
      "A Discord server where you can add DMbot, and a voice channel for your table. You choose the channel it listens in. Plans are on the Prices page.",
    more: { text: "See the plans", href: "/pricing" },
  },
];
