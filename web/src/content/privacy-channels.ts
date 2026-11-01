// Privacy lines for features that hear or show something new (#1810). Each is shown only while its
// feature is live, with the same switch as its Help steps and What's new entry (help.ts), so the page
// never states something that isn't true yet. Session notes (#3027) have no Help step yet: their line
// follows SESSION_NOTES_LIVE, the switch of its What's new entry. Plain words: a few lines each, no technical terms.
import { INITIATIVE_STYLE_LIVE, STORY_STATES_LIVE } from "./help";
import { SESSION_NOTES_LIVE } from "./session-notes-switch";

/** In "What we keep": the initiative style FightBot hears (#1783) and keeps per campaign (#1790). */
export const initiativeLine = {
  live: INITIATIVE_STYLE_LIVE,
  lead: "How your table runs initiative.",
  text: "FightBot listens to the table (only people who said yes) for how you take turns: one at a time, by side, or rolled each round. It uses no AI for this. DMbot keeps the last style your game master's words set, as one short label on the campaign, not tied to any person. A backup holds it, and deleting the campaign deletes it.",
};

/** In "What we keep": who is alive, dead or destroyed and who carries what (#1284; core memory/states.py, memory/backup.py). */
export const storyStatesLine = {
  live: STORY_STATES_LIVE,
  lead: "Who is alive and who carries what.",
  text: "For each campaign, DMbot keeps whether a character, item or place is alive, dead or destroyed, and who carries the one-of-a-kind things. It keeps only what your game master said yes to or set, with when it changed and who said so. Questions about it go to the game master only. It stays in that campaign: it never goes to another campaign or server, and it never goes into a transcript. A backup holds it, and deleting the campaign deletes it.",
};

/** The two sentences of the privacy page that name session notes (#3027); the old sentences show while the switch is off. */
export const sessionNotesPrivacy = {
  live: SESSION_NOTES_LIVE,
  makes:
    "That means a recap, session notes (a numbered list of what happened in each game), story facts, notes for the game master, and anything else DMbot writes from what was said.",
  stays: "Recaps (short summaries of a game) and session notes (a numbered list of what happened in each game) stay.",
};
