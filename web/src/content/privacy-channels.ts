// Privacy lines for features that hear or show something new (#1810). Each is shown only while its
// feature is live, with the same switch as its Help steps and What's new entry (help.ts), so the page
// never states something that isn't true yet. Plain words: a few lines each, no technical terms.
import { INITIATIVE_STYLE_LIVE, NPCS_CHANNEL_LIVE, STORY_STATES_LIVE } from "./help";

/** In "Who can see it": the people list channel (docs/PLAN.md, "Channels"; core dm_screen/retired_channels.py). */
export const peopleListLine = {
  live: NPCS_CHANNEL_LIVE,
  lead: "The people list.",
  text: "DMbot can keep a read-only channel that lists only the people and groups your game master confirmed, with their other names, the groups they are in and who they are connected to. It never shows secrets, notes, or whether someone is dead or missing. Who can read it follows your game master's DM screen setting: only the game masters, players who choose to peek, or everyone in the server.",
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
