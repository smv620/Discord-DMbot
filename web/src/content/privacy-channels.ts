// Privacy lines for features that hear or show something new (#1810). Each is shown only while its
// feature is live, with the same switch as its Help steps and What's new entry (help.ts), so the page
// never states something that isn't true yet. Plain words: a few lines each, no technical terms.
import { INITIATIVE_STYLE_LIVE, NPCS_CHANNEL_LIVE } from "./help";

/** In "Who can see it": the people list channel (docs/PLAN.md, "Channels"; core dm_screen/npcs_channel.py). */
export const peopleListLine = {
  live: NPCS_CHANNEL_LIVE,
  lead: "The people list.",
  text: "DMbot can keep a read-only channel that lists the people and groups your game master confirmed, with their other names, the groups they are in and who they are connected to. It never shows secrets, notes, or what has happened to someone. Who can read it follows the same setting as the DM screen: only game masters, players who peek, or everyone in the server.",
};

/** In "What we keep": the initiative style FightBot hears (#1783) and keeps per campaign (#1790). */
export const initiativeLine = {
  live: INITIATIVE_STYLE_LIVE,
  lead: "How your table runs initiative.",
  text: "FightBot listens for how your table takes turns: one at a time, by side, or rolled each round. It hears only people who said yes, and uses no AI for this. It keeps one short word with the campaign, the last style your game master's words set. A backup holds it.",
};
