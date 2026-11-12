// Privacy lines for features that hear or show something new (#1810). Each is shown only while its
// feature is live, with the same switch as its Help steps and What's new entry (help.ts), so the page
// never states something that isn't true yet. Session notes (#3027) have no Help step yet: their line
// follows SESSION_NOTES_LIVE, the switch of its What's new entry. Plain words: a few lines each, no technical terms.
import { REMEMBERED_PLAYERS_LIVE } from "./remembered-players-switch";
import { INITIATIVE_STYLE_LIVE, STORY_STATES_LIVE } from "./help";
import { IMPORT_CONFIRM_LIVE } from "./import-words";
import { PC_ICONS_LIVE } from "./pc-icons-switch";
import { SPEAKER_REMOVAL_LIVE } from "./speaker-removal-switch";
import { SESSION_NOTES_LIVE } from "./session-notes-switch";

/** In "What we keep": the lines of older sessions a game master imports (#3306). It follows the switch of the page that
 * lets the uploader confirm each speaker, so the page never says what DMbot does not do yet. */
export const importConsentLine = {
  live: IMPORT_CONFIRM_LIVE,
  lead: "Older sessions a game master imports:",
  text: `If you say no to being recorded, DMbot remembers it, so nobody can add your lines from an uploaded file. A game master can keep your lines from an uploaded file only if you agreed in Discord, or if they tick that you agreed. The tick is the game master's word; DMbot does not ask you. Lines are kept in that one campaign, under your name as written in the file.${SPEAKER_REMOVAL_LIVE ? " A server member can delete their own lines from Discord; anyone else asks the game master, who can remove them. Lines a game master removes from a campaign stay removed from that campaign, even if it is restored from an older backup. A copy made before the removal and restored as a new campaign can still hold those lines, and the game master removes them again there." : ""}`,
};

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

/** In "What we keep": the pictures on a campaign's Story Graph (#3622). They load straight from Discord or dndbeyond.com, which the
 * site's security policy allows by exact host (public/_headers). Shown only while the icons are live, with their switch. */
export const pcIconsLine = {
  live: PC_ICONS_LIVE,
  lead: "Pictures of player characters.",
  text: "On a campaign's Story Graph (the map of names and how they connect), a player's character can show that player's Discord profile picture, if they said yes to being recorded, or the portrait from its dndbeyond.com sheet. Anyone who can see that character on the graph sees the picture. DMbot keeps only the link, never the picture: your browser loads it from Discord or dndbeyond.com when you open the graph, and they can see that you did. The player whose character it is, or the game master, can hide it. If the player presses Stop recording me, their Discord picture stops showing. A backup keeps only the choice, never a picture, and deleting the campaign deletes the choices.",
};

/** In "What we keep": the list a DM keeps of the players they have played with (#4226; owner, 2026-11-12). Shown with its switch. */
export const rememberedPlayersLine = {
  live: REMEMBERED_PLAYERS_LIVE,
  lead: "Who you have played with.",
  text: "A DM you play with keeps your Discord name on a list, so they can add you to their next campaign. You can remove yourself at any time. The list holds only your name, when you first played and how many campaigns, never a character or a story, and only that DM can see it.",
};
