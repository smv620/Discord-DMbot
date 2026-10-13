// The Help page (#1142): how to use DMbot in Discord, step by step. Every button and command
// name is copied from what the bot shows, and web/test/help.test.ts checks each one still
// appears in core/src/dmbot, so the page can't drift. Only built features; no game trademarks
// (CLAUDE.md), so the character-sheet steps name no company.
import { minimumAge } from "./legal";

export interface Step {
  /** The words people read. Names in `names` must appear in the bot's code. */
  text: string;
  /** Exact button or command names used in `text`, with the bot file that shows each. */
  names?: { name: string; file: string }[];
}

export const helpHeading = "How to use DMbot";
export const helpIntro =
  "Everything happens in Discord. These are the buttons and commands exactly as DMbot shows them.";

const consent = `I'm ${minimumAge} or older, record me`;

export const dmSteps: Step[] = [
  { text: "Add DMbot to your Discord server from the Add to Discord page." },
  {
    text: "Join your table's voice channel and type /dmbot start. Then press ▶ Continue last campaign, or ＋ New campaign to make one.",
    names: [
      { name: "/dmbot start", file: "bot.py" },
      { name: "▶ Continue last campaign", file: "ui/logic.py" },
      { name: "＋ New campaign", file: "ui/dmbot_commands.py" },
    ],
  },
  {
    text: "Pick the voice channel, then press ▶ Start listening. Players get a private message asking if they say yes. Only people who say yes are recorded.",
    names: [{ name: "▶ Start listening", file: "ui/dmbot_commands.py" }],
  },
  {
    text: "Press ⚙️ Settings on your DM screen to change the game clock, timers, house rules and who can peek behind the DM screen.",
    names: [{ name: "Settings", file: "dm_screen/settings.py" }],
  },
  {
    text: 'Ask a quick question out loud: "Hey DMbot, how long does Bless last?" You get a short private answer that says how sure DMbot is.',
  },
  {
    text: "When the game is over, type /dmbot stop.",
    names: [{ name: "/dmbot stop", file: "bot.py" }],
  },
  {
    text: "To keep a copy of a campaign, type /dmbot backup. To give a campaign to another game master, press ⚙️ Settings, then Hand over. They must say yes.",
    names: [
      { name: "/dmbot backup", file: "ui/dmbot_commands.py" },
      { name: "Hand over", file: "dm_screen/handover.py" },
    ],
  },
];

export const playerSteps: Step[] = [
  {
    text: `DMbot sends you a private message asking to record you. Press ${consent} to say yes, or No thanks to say no. You can still play if you say no.`,
    names: [
      { name: "record me", file: "consent_words.py" },
      { name: "No thanks", file: "consent_dm.py" },
    ],
  },
  {
    text: "To stop at any time, press ⚙️ Menu in that private message, then Stop recording me, then Yes, stop recording me.",
    names: [
      { name: "Menu", file: "consent_dm.py" },
      { name: "Stop recording me", file: "consent_dm.py" },
      { name: "Yes, stop recording me", file: "consent_dm.py" },
    ],
  },
  {
    text: "Press 📜 My character sheet to link your public online sheet, or to type in your character instead. A linked sheet only works if you set it to Public on the sheet's own site (anyone with the link can open it). The game masters see it on your character's card.",
    names: [{ name: "📜 My character sheet", file: "consent_dm.py" }],
  },
  {
    text: "To read what was said in a game, type /transcript. Anyone in the server can read and download it.",
    names: [{ name: "/transcript", file: "ui/transcripts.py" }],
  },
  {
    text: "Your game master can turn on a game clock. You will see the story's day and time, like \"Day 5, morning\", in your own channel.",
  },
];
