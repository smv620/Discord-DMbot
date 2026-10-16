// The Help page (#1142): how to use DMbot in Discord, step by step. Every button and command
// name is copied from what the bot shows, and web/test/help.test.ts checks each one still
// appears in core/src/dmbot, so the page can't drift. Only built features; no game trademarks
// (CLAUDE.md), so the character-sheet steps name no company.
import { QA_URL } from "./account";
import { minimumAge } from "./legal";
import { price as narrationPrice } from "./recap-audio";
import { RECAP_AUDIO_LIVE, RECAPS_LIVE, emailsIntroTail, recapEmailStep, recapSteps as baseRecapSteps } from "./recap-words";
import { SUPPORT_AI_LIVE, helpNote } from "./support-words";

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
    text: "To say an item, like a magic sword, is the only one, press 🔍 Find a name, open the item, and press ☝️ Only one exists. Press Allow more than one to undo it. An item with its own proper name is taken as the only one until you say otherwise. Only game masters can change this.",
    names: [
      { name: "Find a name", file: "ui/name_card.py" },
      { name: "☝️ Only one exists", file: "ui/name_card.py" },
      { name: "Allow more than one", file: "ui/name_card.py" },
    ],
  },
  {
    text: "A name with a long list of other names or connections shows the first few on its card. Open the Show all ▾ list under the card to read the rest, one page at a time.",
    names: [{ name: "Show all ▾", file: "ui/name_card.py" }],
  },
  {
    text: "To keep a connection from your players, open the name, press 🧭 Connect to…, press 🤫 Secret once, so it reads 🤫 Secret: yes, then pick the other name. Players' cards never show that connection. Only a game master of the campaign can turn Secret on. If a connection is already secret, it stays secret.",
    names: [
      { name: "Connect to…", file: "ui/name_card.py" },
      { name: "🤫 Secret: yes", file: "ui/name_card.py" },
    ],
  },
  {
    text: "To say who has something, open the name, press 🧭 Connect to…, and choose carries. It is saved either way. If it looks odd, for example \"Someone else already has that, and there is only one\", DMbot says so and will ask you about it after the session. DMbot only points it out. You decide.",
    names: [
      { name: "Connect to…", file: "ui/name_card.py" },
      { name: "carries", file: "ui/name_card.py" },
      { name: "Someone else already has that, and there is only one", file: "ui/name_card.py" },
      { name: "will ask you about it after the session", file: "ui/name_card.py" },
    ],
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
    text: "To share your house rules with another table, press ⚙️ Settings, then 📚 Shared rules, then 📤 Share my house rules (paid campaigns only). DMbot gives you a share code. Anyone with the code can read your rules, so give it only to a game master you trust. Press 🔑 New share code to get another one. Old codes keep working; Stop sharing turns them all off. If your table has relationships of its own (kinds of link between people, places and things), DMbot shows them as a list before sharing. Tick the ones to share and press Share with ticked, or press Rules only. Only their names and what they mean go; never your story, your characters' or places' names, or your notes.",
    names: [
      { name: "Shared rules", file: "dm_screen/settings.py" },
      { name: "📤 Share my house rules", file: "ui/shared_rules.py" },
      { name: "🔑 New share code", file: "ui/shared_rules.py" },
      { name: "Share with ticked", file: "ui/shared_rules_defs.py" },
      { name: "Rules only", file: "ui/shared_rules_defs.py" },
    ],
  },
  {
    text: "To use another table's rules, press ⚙️ Settings, then 📚 Shared rules, then 📥 Use shared rules, and type their code. DMbot shows you the rules first. Press Yes, use this set to keep them. Your own house rules always win over theirs. If the set brings relationships, DMbot tells you which. You can't change them while you use the set. One you already have by the same name stays yours.",
    names: [
      { name: "📥 Use shared rules", file: "ui/shared_rules.py" },
      { name: "Yes, use this set", file: "ui/shared_rules.py" },
    ],
  },
  {
    text: "When shared rules change, DMbot adds the new version at your next session and tells you. Press See changes to look, Pick changes to choose which to keep, or Undo to put your old copy back. Relationships come one at a time like rules.",
    names: [
      { name: "See changes", file: "dm_screen/set_update.py" },
      { name: "Pick changes", file: "dm_screen/set_update.py" },
      { name: "Undo", file: "dm_screen/set_update.py" },
    ],
  },
  {
    text: "To suggest one of your own house rules to the game master whose rules you use, type /dmbot houserules, press 💡 Suggest next to your rule, then press Yes, send it. It goes exactly as written, with no name on it, so check that the rule names no one. Your own rules stay as they are.",
    names: [
      { name: "/dmbot houserules", file: "ui/house_rules.py" },
      { name: "💡 Suggest", file: "ui/house_rules.py" },
      { name: "Yes, send it", file: "ui/house_rules.py" },
    ],
  },
  {
    text: "You hear the answer on your DM screen at your next session. You only see 💡 Suggest for rules your table wrote, and only while your copy still gets updates.",
  },
  {
    text: "When another table suggests a rule for the rules you share, a note appears on your DM screen. It never says which table sent it. Press See to preview the change, Add to set to make a new version for every table that uses your rules, or No thanks. Nothing changes until you press Add to set.",
    names: [
      { name: "See", file: "dm_screen/set_suggestion.py" },
      { name: "Add to set", file: "dm_screen/set_suggestion.py" },
      { name: "No thanks", file: "dm_screen/set_suggestion.py" },
    ],
  },
  {
    text: "If the rules you use stop being shared, your table keeps its copy and the rules keep working, but they won't get updates any more. The 📚 Shared rules screen says \"This copy no longer gets updates.\" To get updates again, ask that game master to share the rules again and send you the new code. Then press Stop using shared rules (your own rules stay, and Undo puts the copy back), then 📥 Use shared rules, and type the code.",
    names: [
      { name: "This copy no longer gets updates", file: "ui/shared_rules.py" },
      { name: "Stop using shared rules", file: "ui/shared_rules.py" },
      { name: "📥 Use shared rules", file: "ui/shared_rules.py" },
    ],
  },
  {
    text: "A backup keeps the rules your table uses: the set's name, the version you had, and its rules. It does not keep the share code. After /dmbot restore, the Shared rules screen says \"This copy no longer gets updates\", and the rules keep working. To get updates again, ask the game master for a share code. Press Stop using shared rules, then 📥 Use shared rules, and type the code.",
    names: [
      { name: "/dmbot restore", file: "ui/dmbot_commands.py" },
      { name: "Stop using shared rules", file: "ui/shared_rules.py" },
    ],
  },
  {
    text: "To stop using someone's rules, press ⚙️ Settings, then 📚 Shared rules, then Stop using shared rules. Your own rules stay as they are. Their relationships go too, unless a fact of yours uses one: that one stays, marked, and can't be used for new facts.",
    names: [{ name: "Stop using shared rules", file: "ui/shared_rules.py" }],
  },
  {
    text: "To stop sharing your own rules, press ⚙️ Settings, then 📚 Shared rules, then Stop sharing, then Yes, stop sharing. Every share code stops working. Tables already using your rules keep their copy, but it won't be updated any more.",
    names: [
      { name: "Stop sharing", file: "ui/shared_rules.py" },
      { name: "Yes, stop sharing", file: "ui/shared_rules.py" },
    ],
  },
  {
    text: "You can choose how long a campaign keeps its transcripts: Forever, 1 year, 90 days or 30 days (⚙️ Settings → 📜 Transcripts). Older ones are deleted within a day. Recaps stay.",
    names: [{ name: "Transcripts", file: "dm_screen/transcript_keep.py" }],
  },
  {
    text: "To share your homebrew with another table, press ⚙️ Settings, then 📚 Shared rules, then 📦 Homebrew packs, then 📤 Share my homebrew (paid campaigns only). DMbot gives you a share code. Any game master in your server can read everything you share, and so can anyone you give the code to. That includes entries you hide from players. Only share what you are happy for them to see. Followers get a new version only when you press 🔄 Publish my changes. Only homebrew is shared, never your story, names or notes.",
    names: [
      { name: "📦 Homebrew packs", file: "ui/shared_rules.py" },
      { name: "📤 Share my homebrew", file: "ui/shared_packs.py" },
      { name: "🔄 Publish my changes", file: "ui/shared_packs.py" },
    ],
  },
  {
    text: "To use another table's homebrew, press ⚙️ Settings, then 📚 Shared rules, then 📦 Homebrew packs, then 📥 Follow a pack, and type the share code they gave you. DMbot shows you what is in the pack first. Press Yes, follow this pack only if you want it. If you have homebrew with the same name, yours is used. Press Stop following any time to stop.",
    names: [
      { name: "📥 Follow a pack", file: "ui/shared_packs.py" },
      { name: "Yes, follow this pack", file: "ui/shared_packs.py" },
      { name: "Stop following", file: "ui/shared_packs.py" },
    ],
  },
  {
    text: "When a pack you follow changes, DMbot tells you in one note at the start of your next session. The changes are added unless you step in. Press Pick changes to choose which ones to keep, or Undo to put things back how they were.",
    names: [
      { name: "Pick changes", file: "dm_screen/pack_update.py" },
      { name: "Undo", file: "dm_screen/pack_update.py" },
    ],
  },
  {
    text: "To change an entry that comes from a pack you follow, look it up (for example with /dmbot rule) and press ✏️ Make it my own on its card. DMbot copies it into your campaign's own homebrew and opens it so you can edit it. From then on this campaign uses your copy instead of the pack's. The pack itself does not change.",
    names: [{ name: "✏️ Make it my own", file: "ui/rule_lookup.py" }],
  },
  {
    text: "To suggest one of your own homebrew entries to the pack you follow, open the entry in your homebrew and press 💡 Suggest to the pack. The game master who shares the pack sees the entry with Add to pack or No thanks. They never see who sent it. The pack stays as it is unless they press Add to pack.",
    names: [
      { name: "💡 Suggest to the pack", file: "ui/homebrew.py" },
      { name: "Add to pack", file: "dm_screen/pack_suggestion.py" },
      { name: "No thanks", file: "dm_screen/pack_suggestion.py" },
    ],
  },
  {
    text: "To give a campaign to another game master, press ⚙️ Settings, then Hand over. They must say yes.",
    names: [{ name: "Hand over", file: "dm_screen/handover.py" }],
  },
];

/**
 * What a quick question to DMbot can use (#1379), under the game master's steps. Each sentence is
 * what the code does after #1377 and #1380: sidebar/context.py (the material a question is given:
 * rules, house rules, names, the scene; recall for the players' audience, never secrets or entry
 * notes), sidebar/answer.py (NO_INFO_ANSWER, and the rule "use only the material given") and
 * sidebar/service.py (the question and the in-game answer are saved in the raw transcript).
 */
export const quickAnswersHeading = "What DMbot knows when you ask it";
export const quickAnswers: Step[] = [
  {
    text: "Quick answers work while your game is running and you have said yes to being recorded.",
  },
  {
    text: 'Message DMbot privately, by voice or by typing, and the answer comes to you alone. Say "Hey DMbot" when the table has a dmb-chat channel, and the answer is posted there for everyone. It then uses only the free rules, your house rules and what was just said. If it needs your story, you get that answer privately.',
  },
  {
    text: "DMbot answers from the free rules, your house rules, what was said in the last few minutes, and the story facts you have confirmed about the names in your question. It leaves out names you marked secret, and your own notes on names and places.",
  },
  {
    text: 'If an answer is not from DMbot\'s rules, it says "check your book". When it has nothing to go on, it says so, for example "I don\'t have that. Your call." You decide.',
    names: [
      { name: "check your book", file: "sidebar/answer.py" },
      { name: "I don't have that. Your call.", file: "sidebar/answer.py" },
    ],
  },
  {
    text: "Your question, and DMbot's answer when it is about the game, are saved in the game's full written record. Everyone in your Discord server can read that record.",
  },
];

/** The recaps steps, with the narration add-on's price from its one constant while narration is on
 * (#1460). The price is never typed here; with none set the sentence leaves it out. */
export const recapSteps: Step[] = [
  ...baseRecapSteps,
  ...(RECAP_AUDIO_LIVE
    ? [
        {
          text: `Narration comes with Guild and Pro. On the other paid plans it is an add-on${
            narrationPrice() ? `, ${narrationPrice()}` : ""
          }. If the audio comes out wrong, ask for it again on the recap page. You can do that once every 10 minutes.`,
        },
      ]
    : []),
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
    text: "Want your own lines gone? In Discord, open the ⚙️ Menu DMbot sends you and press 🗑 Delete my transcripts. It deletes every line you said in that server, from every past game. It can't be undone, and copies someone already saved stay.",
    names: [{ name: "🗑 Delete my transcripts", file: "consent_dm.py" }],
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
    text: "Can't get into your old account any more? After 7 days out of the server, or if Discord says the account is gone, DMbot offers the campaign's other game masters a Take it on button in ⚙️ Settings. A game master chooses whether to press it. Or use a new account: join the server, type /dmbot backup, then /dmbot restore. Both need a paid campaign, and restore needs a free campaign slot on your plan.",
    names: [
      { name: "Take it on", file: "dm_screen/handover.py" },
      { name: "/dmbot backup", file: "ui/dmbot_commands.py" },
      { name: "/dmbot restore", file: "ui/dmbot_commands.py" },
    ],
  },
];

/** Emails from DMbot (#1283): every sentence is read from core/src/dmbot/mail.py, trial_jobs.py and
 * ui/migrate.py. No address appears here: the emails carry the sender's postal address themselves. */
export const emailSteps: Step[] = [
  {
    text: `DMbot sends very few emails. A sign-in link and a move code come only when you ask for one. ${emailsIntroTail}`,
  },
  {
    text: "Sign-in link: ask for it on your account page and type the email you paid with or the email Discord has for you. We send a link that works once and stops working after 15 minutes.",
  },
  {
    text: "Move code: when you move to a new Discord account, run /dmbot migrate on your old account and press Get a move code. It goes to the email you paid with, and nowhere else. It works once and lasts 30 minutes. A move code is only for you: never give it to anyone.",
    names: [
      { name: "/dmbot migrate", file: "ui/migrate.py" },
      { name: "Get a move code", file: "ui/migrate.py" },
    ],
  },
  {
    text: "Didn't ask for a sign-in link or a move code? Ignore the email. Nothing changes.",
  },
  {
    text: "Try It emails: one on the last day of Try It, and one if you get 30 more days. Each has a link to stop emails like it. Sign-in and move-code emails have no such link, because you asked for them.",
  },
  ...(RECAPS_LIVE ? [{ text: recapEmailStep }] : []),
];

/** Said under the list: it is not a step. */
export const emailNote = "For now, emails are sent from DMbot's own Gmail mailbox.";

/** Not a step to do: what players may notice at the table. */
export const playerNote =
  'Your game master may show the story\'s day and time, like "Day 5, morning", in your own channel. You do not need to do anything.';

/** The one support address (docs/EMAIL.md, owner's setup, #1341). Every other place on the site
 * that names a way to write to us uses this constant, and a test fails if a different
 * @getdmbot.com address shows up. */
export const SUPPORT_EMAIL = "support@getdmbot.com";

/** A line of text and its links: plain strings, and link objects in between. */
export type Part = string | { text: string; href: string };

/** Getting help (#1352): one plain place that says where to ask, in the order to try them.
 * No privacy promises are made here: the Support form says its own, and the privacy page holds
 * them. The public Q&A is said to be public, so nobody posts account or payment details. */
export const gettingHelp: { id: string; heading: string; items: { lead: string; parts: Part[] }[] } =
  {
    id: "getting-help",
    heading: "Getting help",
    items: [
      { lead: "Look here first.", parts: ["The steps above may answer it."] },
      {
        lead: "Ask in public.",
        parts: [
          "Post a question in the ",
          { text: "Q&A for DMbot", href: QA_URL },
          ". A helper called QAbot (a bot, not a person) may answer first. Everything there is public, so don't post account or payment details.",
        ],
      },
      {
        lead: "Have an account?",
        parts: [
          "Write to the team in private from ",
          { text: "your account page", href: "/account" },
          ". The team replies by email to the address on your account, if it has one.",
        ],
      },
      {
        lead: "Anyone, on any plan.",
        parts: ["Email ", { text: SUPPORT_EMAIL, href: `mailto:${SUPPORT_EMAIL}` }, "."],
      },
      ...(SUPPORT_AI_LIVE ? [{ lead: "Who answers?", parts: [helpNote] }] : []),
    ],
  };
