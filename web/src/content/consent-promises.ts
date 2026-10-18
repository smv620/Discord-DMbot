// What DMbot's consent request promises every player, in plain sentences (#1882). The /players page and
// Help's player section both show exactly this list, so the site can't promise something different from
// the request a player presses "yes" on (core/src/dmbot/consent_dm.py, request_text; CLAUDE.md, Hard rules:
// consent, transcripts, DM authority).
//
// TERMS_VERSION: 6
// That line must equal TERMS_VERSION in core/src/dmbot/consent.py. core/tests/test_consent_promises.py
// fails when the bot's terms version goes up and this number does not, so whoever changes the terms
// has to read this list and the two pages that show it, then bump the number here.
export interface ConsentPromise {
  /** A stable name for tests and anchors. */
  id: string;
  text: string;
}

export const consentPromisesHeading = "What you agree to";
export const consentPromisesLead = "When you press the button to say yes:";

export const consentPromises: ConsentPromise[] = [
  {
    id: "only-yes",
    text: "DMbot records only people who say yes. It listens to your voice and reads what you type in the table's chat. It never records another bot.",
  },
  {
    id: "ai",
    text: "The written text goes to an AI company (Anthropic) to give your game master notes. It is not used to train their AI.",
  },
  {
    id: "transcript",
    text: "Everyone in your Discord server can read and download the written text of the game (the transcript). It stays there even if you stop later.",
  },
  {
    id: "stop",
    text: "You can stop at any time: press ⚙️ Menu in the private message, then Stop recording me, then Yes.",
  },
  {
    id: "dm-screen",
    text: "DMbot never sends the game master's private notes to players. They are hidden unless your game master allows it, and a Peek button warns you about spoilers first.",
  },
];
