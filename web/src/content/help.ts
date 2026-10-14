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
  "Everything happens in Discord. Buttons and commands are written just as DMbot shows them.";

const consent = `I'm ${minimumAge} or older, record me`;

export const dmSteps: Step[] = [
  { text: "Add DMbot to your Discord server from the Add to Discord page." },
  {
    text: "Type /dmbot start in any text channel. Then press ▶ Continue last campaign, or ＋ New campaign if this is your first game.",
    names: [
      { name: "/dmbot start", file: "bot.py" },
      { name: "▶ Continue last campaign", file: "ui/logic.py" },
      { name: "＋ New campaign", file: "ui/dmbot_commands.py" },
    ],
  },
  {
    text: "Pick your table's voice channel, then press ▶ Start listening. Each player gets a private message asking if they say yes. Only people who say yes are recorded.",
    names: [{ name: "▶ Start listening", file: "ui/dmbot_commands.py" }],
  },
  {
    text: "On your DM screen, press ⚙️ Settings to change the game clock, house rules, or who can peek behind the DM screen.",
    names: [{ name: "Settings", file: "dm_screen/settings.py" }],
  },
  {
    text: "To let DMbot read what people type, press ⚙️ Settings, then Table chat. It reads the voice channel's chat and one more channel you pick, only from people who said yes.",
    names: [{ name: "Table chat", file: "dm_screen/table_chat_panel.py" }],
  },
  {
    text: "After a game, press 🧑 Check who is who. DMbot notices when someone answers to a name several times (\"Mike, what do you do?\" then \"I run with my sword\"). You see who it thinks plays whom and say yes or no. Nothing is used until you say yes.",
    names: [{ name: "Check who is who", file: "ui/people_review.py" }],
  },
  {
    text: 'Ask a quick question out loud: "Hey DMbot, how long does Bless last?" The short answer is posted in the table\'s dmb-chat channel and says how sure DMbot is. You have the final say.',
    names: [{ name: "dmb-chat", file: "dm_screen/dmbchat_channel.py" }],
  },
  {
    text: "When the game is over, type /dmbot stop.",
    names: [{ name: "/dmbot stop", file: "bot.py" }],
  },
  {
    text: "To keep a copy of a campaign, type /dmbot backup (paid campaigns only).",
    names: [{ name: "/dmbot backup", file: "ui/dmbot_commands.py" }],
  },
  {
    text: "To give a campaign to another game master, press ⚙️ Settings, then Hand over. They must say yes.",
    names: [{ name: "Hand over", file: "dm_screen/handover.py" }],
  },
];

export const playerSteps: Step[] = [
  {
    text: `DMbot sends you a private message. Press ${consent} to say yes.`,
    names: [{ name: "record me", file: "consent_words.py" }],
  },
  {
    text: "Want to say no? Press No thanks. You can still play.",
    names: [{ name: "No thanks", file: "consent_dm.py" }],
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
    text: "While DMbot is listening, it also reads what you type in the table's chat, if you said yes. Nothing is read from anyone who said no.",
  },
  {
    text: 'Anyone at the table who said yes can ask DMbot a quick rules question: say "Hey DMbot" or type it in the dmb-chat channel. Your question is posted there with your name. Your game master has the final say.',
    names: [{ name: "dmb-chat", file: "dm_screen/dmbchat_channel.py" }],
  },
  {
    text: "When you ask DMbot a question in dmb-chat, it shows the name of the character you play once your game master has said yes to it. Until then it shows your server nickname.",
  },
  {
    text: "Everyone in the server can read a game's transcript. To read or download one, type /transcript.",
    names: [{ name: "/transcript", file: "ui/transcripts.py" }],
  },
  {
    text: "Press 📜 My character sheet to link your online sheet, or to type your character in.",
    names: [{ name: "📜 My character sheet", file: "consent_dm.py" }],
  },
  {
    text: "A linked sheet must be set to Public on the sheet's own site, so anyone with the link can open it. Your game master sees it on your character's card.",
  },
  {
    text: "In a fight, DMbot's fight helper, FightBot, may send you a short private reminder of what your sheet says you still have, like a spell, a feature, or spell slots. It only reminds you. Your game master decides what happens. To switch it off, press 📜 My character sheet, then Turn FightBot off.",
    names: [{ name: "Turn FightBot off", file: "ui/sheets.py" }],
  },
  {
    text: "If your sheet is linked online, your spell slots show on your character card. About a minute after someone at the table says a slot was used, DMbot looks at your sheet again to keep up. It never changes your sheet.",
  },
];

/** Your account (#1215): DMbot has no password of its own, and a new Discord account starts clean. */
export const accountSteps: Step[] = [
  {
    text: "Forgot your password? DMbot has no password. It uses your Discord account, so use Discord's own reset: open the Discord sign-in page and press Forgot your password?",
  },
  {
    text: "Moving to a new Discord account? Do this for each campaign you own: press ⚙️ Settings, then press Hand over, and pick your new account or another game master. When all are handed over, cancel your plan on your account page. On the new account, say yes to being recorded again.",
    names: [{ name: "Hand over", file: "dm_screen/handover.py" }],
  },
  {
    text: "Can't reach your old account any more? If it has been out of your server for 7 days, or Discord says it is gone, DMbot gives the campaign's other game masters a Take it on button in ⚙️ Settings. A new account can also type /dmbot backup once it has joined the server, then /dmbot restore (that needs a plan with a free campaign slot). Backups are for paid campaigns.",
    names: [
      { name: "Take it on", file: "dm_screen/handover.py" },
      { name: "/dmbot backup", file: "ui/dmbot_commands.py" },
      { name: "/dmbot restore", file: "ui/dmbot_commands.py" },
    ],
  },
];

/** Not a step to do: what players may notice at the table. */
export const playerNote =
  'Your game master may show the story\'s day and time, like "Day 5, morning", in your own channel. You do not need to do anything.';
