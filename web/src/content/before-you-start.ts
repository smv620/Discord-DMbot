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
    // Stop button is on the private consent message (consent_dm.py; the Help page's player steps).
    question: "Does it record everyone?",
    answer:
      "No. Only people who press yes are recorded, and bots never are. Anyone can stop at any time: press ⚙️ Menu in DMbot's private message, then Stop recording me.",
  },
  {
    // CLAUDE.md "Transcripts are shared; the DM screen is not pushed to players"; the three
    // choices are the buttons in dm_screen/messages.py (VISIBILITY_BUTTONS, WHO_CAN_SEE), and
    // "peek" is the default (schema.py, campaigns.dm_screen_visibility).
    question: "Can my players see my notes?",
    answer:
      "Not unless you let them. You choose: only you, players can peek after a spoiler warning (the usual setting), or everyone in the server.",
  },
  {
    // CLAUDE.md "DM authority" and "Citations": tips go only to the DM screen, with a source.
    question: "Does DMbot make rulings or tell the story?",
    answer:
      "No. It sends the game master quiet tips with where they come from, and the game master decides. It never makes up story.",
  },
  {
    // CLAUDE.md "Transcripts are shared"; the words the yes message uses (consent_dm.py: "Anyone
    // in this server can read and download that text"). The privacy page has the rest.
    question: "Who can read what was said?",
    answer:
      "Everyone in your Discord server can read and download a game's written record, and the message that asks players to say yes tells them so. Only people who said yes are in it.",
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
    // price is repeated here.
    question: "What do I need?",
    answer:
      "A Discord server where you can add DMbot, and a voice channel for your table. You choose the channel it listens in.",
    more: { text: "See the plans", href: "/pricing" },
  },
];
