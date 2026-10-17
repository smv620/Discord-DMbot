// The Help page (#1142): how to use DMbot in Discord, step by step. Every button and command
// name is copied from what the bot shows, and web/test/help.test.ts checks each one still
// appears in core/src/dmbot, so the page can't drift. Only built features; no game trademarks
// (CLAUDE.md), so the character-sheet steps name no company.
import { QA_URL } from "./account";
import { minimumAge } from "./legal";
import { price as narrationPrice } from "./recap-audio";
import { RECAP_AUDIO_LIVE, RECAPS_LIVE, emailsIntroTail, recapEmailStep, recapSteps as baseRecapSteps } from "./recap-words";
import { SUPPORT_AI_LIVE, helpNote } from "./support-words";
import { portalLoginSteps, portalLoginUrl } from "./billing-login";

export interface Step {
  /** The words people read. Names in `names` must appear in the bot's code. */
  text: string;
  /** Exact button or command names used in `text`, with the bot file that shows each. */
  names?: { name: string; file: string }[];
  /** A page on this website the step points to (shown after the text). */
  link?: { text: string; href: string };
}

export const helpHeading = "How to use DMbot";
export const helpIntro =
  "Everything happens in Discord. Buttons and commands are written just as DMbot shows them.";

const consent = `I'm ${minimumAge} or older, record me`;

// Adding homebrew from a link (#1634, #1635, #1645, #1652) went live on 2026-10-17, after the
// owner tried it (#1664). Its Help steps go with the What's new entries "Add homebrew from a link",
// "Share imported homebrew in a pack" and "See the sites you approved" (whats-new.ts); a test
// keeps the switch and those entries in step.
export const HOMEBREW_LINKS_LIVE = true;

export const homebrewLinkSteps: Step[] = [
  {
    text: "To add homebrew from a web page, press ⚙️ Settings, then 📚 Homebrew, then 🔗 Add from a link, and paste the address of a published homebrew page. If the site says the page isn't public, publish it there first. DMbot reads that one page and shows it to you. It is saved only when you press Save. The first time for a site, DMbot asks you to press I have the right. After that it never asks again for that site in this campaign.",
    names: [
      { name: "🔗 Add from a link", file: "ui/homebrew.py" },
      { name: "Save", file: "ui/homebrew.py" },
      { name: "I have the right", file: "ui/homebrew.py" },
    ],
  },
  {
    text: "An entry added from a link keeps the page's words, so you can't edit them in DMbot. To fetch the page again, open the entry and press 🔄 Update from the link. Every table that uses that page gets the update.",
    names: [{ name: "🔄 Update from the link", file: "ui/homebrew.py" }],
  },
  {
    text: "A homebrew pack can carry entries added from links. If you follow such a pack, DMbot tells you which site its entries come from and waits. Press the Use button for the site (for example Use example.com) and then I have the right. Until you do, DMbot skips those entries.",
    names: [
      { name: "Use ", file: "ui/shared_packs.py" },
      { name: "I have the right", file: "ui/shared_packs.py" },
    ],
  },
  {
    text: "To see which sites your campaign said it may use homebrew from, press ⚙️ Settings, then 📚 Homebrew, then 🌐 Approved sites. Each site shows who said yes and when. To take one back, pick it from the Take back a site list, then press Take it back. DMbot keeps the entries but stops using them until a game master says yes again. Nothing is deleted.",
    names: [
      { name: "🌐 Approved sites", file: "ui/homebrew.py" },
      { name: "Take back a site", file: "ui/approved_sites.py" },
      { name: "Take it back", file: "ui/approved_sites.py" },
    ],
  },
];

// Secrets only some people know (#1649) need the story extractor, which stays off until its
// prompt passes the measurement (#1282), so the step is held with the What's new entry "Secrets
// only some people know". Set this to true in the same change that moves that entry out of `held`.
export const SECRET_CLUES_LIVE = false;

export const secretClueSteps: Step[] = [
  {
    text: "To say that only some characters know a secret or clue, open the secret's card and press 🔒 Only some know this. Press ➕ Add who knows to list them, then press 🔒 Only some know to switch it on. After a session, DMbot tells you, privately, if someone who isn't listed spoke of it. DMbot never decides who learned it. You do. Press 🔓 Let anyone know to stop tracking it.",
    names: [
      { name: "🔒 Only some know this", file: "ui/name_card.py" },
      { name: "➕ Add who knows", file: "ui/clue_audience.py" },
      { name: "🔒 Only some know", file: "ui/clue_audience.py" },
      { name: "🔓 Let anyone know", file: "ui/clue_audience.py" },
    ],
  },
  {
    text: "If a player asks DMbot about it, DMbot won't show a clue that only some people know.",
  },
];

// The DMbot channel (#1666, #1676) moves the buttons off the DM screen, so every Help line that
// sent people to ⚙️ Settings "on the DM screen" changes with it. It is held with the What's new
// entry "A DMbot channel for buttons" (whats-new.ts, `held`) until it has been seen on the test
// server. Set this to true in the same change that moves that entry out of `held`; a test keeps
// the two in step and checks that no line points both ways.
export const DM_BOT_CHANNEL_LIVE = false;

export const settingsOnDmScreenSteps: Step[] = [
  {
    text: "On your DM screen, press ⚙️ Settings to change the game clock, house rules, or who can peek behind the DM screen.",
    names: [{ name: "Settings", file: "dm_screen/settings.py" }],
  },
  {
    text: "Can't find ⚙️ Settings? Type /dmbot settings in your server and it opens for you, privately. It is also on the voice-channel step after /dmbot start. Only the campaign's game masters can use it.",
  },
];

export const dmBotChannelSteps: Step[] = [
  {
    text: "Each campaign has a channel called dmb-dm-bot. Everyone can read it, and only DMbot posts there. It holds one message with two buttons. Press 👤 My buttons to see the buttons for your part at the table. Only you see them. Press ❓ Help to read about DMbot.",
    names: [
      { name: "👤 My buttons", file: "dm_screen/dm_bot_panel.py" },
      { name: "❓ Help", file: "dm_screen/dm_bot_panel.py" },
    ],
  },
  {
    text: "Everyone also gets 📖 Look up a rule and 📜 Transcripts. If you said yes to being recorded, you get ⚙️ Menu, where you can stop being recorded. Players also get 📜 My character sheet. Game masters get ⚙️ Settings too.",
    names: [
      { name: "⚙️ Menu", file: "consent_dm.py" },
      { name: "📜 My character sheet", file: "ui/sheets.py" },
    ],
  },
  {
    text: "Press 📖 Look up a rule to check a rule. Only you see the answer. Players' answers end with \"Your DM decides what applies at your table.\" Press 📜 Transcripts to open the Transcripts page on the DMbot website.",
    names: [
      { name: "📖 Look up a rule", file: "dm_screen/dm_bot_panel.py" },
      { name: "📜 Transcripts", file: "dm_screen/dm_bot_panel.py" },
      { name: "Your DM decides what applies at your table.", file: "ui/rule_card.py" },
    ],
  },
  {
    text: "To change the game clock, house rules, or who can see the DM screen, press 👤 My buttons in the DMbot channel, then ⚙️ Settings.",
    names: [{ name: "Settings", file: "dm_screen/settings.py" }],
  },
  {
    text: "Can't find ⚙️ Settings? Type /dmbot settings in your server and it opens for you, privately. Only the campaign's game masters can use it.",
  },
  {
    text: "Your DM screen is quieter now. It holds only notes, alerts and ⏹ Stop listening. The other buttons moved to the DMbot channel.",
    names: [{ name: "Stop listening", file: "dm_screen/messages.py" }],
  },
];

// Story threads, quiet threads, promises and Led to (#1662, #1674, #1675, #1679) went live on
// 2026-10-17, but their What's new lines stay held until they have been seen on the test server, so
// Help follows the same way (#1694). Set this to true in the same change that moves "Story threads
// you keep" and "Keep track of promises made at the table" out of `held`; a test ties the two.
export const STORY_THREADS_LIVE = false;

export const storyThreadSteps: Step[] = [
  {
    text: "To keep track of a quest, mystery, hook or goal, open the Names list and press 🧵 New thread. On its card, 🧵 Change state says how far along it is. 👥 Who's involved adds people or places. 📌 Link an event adds what moved it.",
    names: [
      { name: "🧵 New thread", file: "ui/names.py" },
      { name: "🧵 Change state", file: "ui/name_card.py" },
      { name: "👥 Who's involved", file: "ui/name_card.py" },
      { name: "📌 Link an event", file: "ui/name_card.py" },
    ],
  },
  {
    text: "After a session, DMbot names threads that haven't moved for a few sessions and shows a 🧵 Check the threads button. The note before your next session has a 🧵 Threads button. Either one opens your list, where you can say Still going, Resolved or Dropped. DMbot only reminds you. You decide.",
    names: [
      { name: "🧵 Check the threads", file: "ui/threads.py" },
      { name: "🧵 Threads", file: "ui/threads.py" },
    ],
  },
  {
    text: "To note something someone swore to do, open the Names list and press 🤝 Add a promise. Then press 🤝 Who promised, 🤝 Promised to and 📅 Due. A promise shows as kept, broken or open. Pick Due soon under 🧵 Change state to have it named in the note before your next session.",
    names: [
      { name: "🤝 Add a promise", file: "ui/names.py" },
      { name: "🤝 Who promised", file: "ui/name_card.py" },
      { name: "🤝 Promised to", file: "ui/name_card.py" },
      { name: "📅 Due", file: "ui/name_card.py" },
      { name: "Due soon", file: "memory/threads.py" },
    ],
  },
  {
    text: "On a clue, an event or a thread, press ➡ Led to… to say what it led to. Cards then show Came from and Led to lines.",
    names: [
      { name: "➡ Led to…", file: "ui/name_card.py" },
      { name: "Came from", file: "ui/name_card.py" },
    ],
  },
  {
    text: "DMbot never sends your threads, promises or these links to players.",
  },
];

// How NPCs feel about the party (#1655) and the homebrew kinds menu (#1663, #1665) went live on
// 2026-10-17 with their What's new lines held until seen on the test server (#1695), so Help is
// held the same way. Each switch flips in the same change that moves its held entries to `entries`;
// a test ties each to them.
export const STANDING_LIVE = false;

export const standingSteps: Step[] = [
  {
    text: "To see how an NPC, a group or a place feels about the party, open its name card and press 🤝 Standing. It shows Hostile, Unfriendly, Wary, Friendly or Devoted for each player character, and for the party as a whole, with the reasons. Press Toward a character… to set it yourself, for one character or the party. DMbot only reminds you. You decide how they act. Players never see it.",
    names: [
      { name: "🤝 Standing", file: "ui/standing.py" },
      { name: "Hostile", file: "memory/standing.py" },
      { name: "Unfriendly", file: "memory/standing.py" },
      { name: "Wary", file: "memory/standing.py" },
      { name: "Friendly", file: "memory/standing.py" },
      { name: "Devoted", file: "memory/standing.py" },
      { name: "Toward a character…", file: "ui/standing.py" },
    ],
  },
];

export const HOMEBREW_KINDS_LIVE = false;

export const homebrewKindSteps: Step[] = [
  {
    text: "Press ⚙️ Settings, then 📚 Homebrew. You see a button for each kind that has entries, like Spells 10/12, which means 10 of 12 spells are on. Press one to see only those. Press ↩ All kinds to go back.",
    names: [
      { name: "Spells", file: "ui/homebrew.py" },
      { name: "↩ All kinds", file: "ui/homebrew.py" },
    ],
  },
  {
    text: "When you add homebrew, you can pick Species, Subclass or Background as the kind. Pages you add from a link are sorted into the right kind too.",
    names: [
      { name: "Species", file: "ui/homebrew.py" },
      { name: "Subclass", file: "ui/homebrew.py" },
      { name: "Background", file: "ui/homebrew.py" },
    ],
  },
];

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
  ...(DM_BOT_CHANNEL_LIVE ? dmBotChannelSteps : settingsOnDmScreenSteps),
  {
    text: "To let DMbot read what people type, press ⚙️ Settings, then Table chat. It reads the voice channel's chat and one more channel you pick, only from people who said yes.",
    names: [{ name: "Table chat", file: "dm_screen/table_chat_panel.py" }],
  },
  {
    text: "To keep track of the game's day and hour, press ⚙️ Settings, then Game clock, and pick a day and an hour. DMbot pins one message on your DM screen, like \"🕰️ Day 4, afternoon (14:30)\". It has the buttons +10 min, +1 hour, Rests & time… and Start a timer. Short rest, Long rest and Skip to dawn are inside the Rests & time… menu. Only the campaign's game masters can press them. Players see just the day and a rough time of day, like \"afternoon\".",
    names: [
      { name: "Game clock", file: "dm_screen/clock.py" },
      { name: "+10 min", file: "dm_screen/clock.py" },
      { name: "+1 hour", file: "dm_screen/clock.py" },
      { name: "Rests & time…", file: "dm_screen/clock.py" },
      { name: "Short rest", file: "dm_screen/clock.py" },
      { name: "Long rest", file: "dm_screen/clock.py" },
      { name: "Skip to dawn", file: "dm_screen/clock.py" },
    ],
  },
  {
    text: "The clock also moves when you, the game master, clearly say it at the start of a line, like \"we take a long rest\", \"an hour later\", \"the next morning\" or \"three days later\". DMbot moves the clock and adds a one-line note with an Undo button. A question, a \"maybe\", or a player saying it never moves the clock. Days passing count as a rest unless you say \"without rest\".",
    names: [{ name: "Undo", file: "dm_screen/clock.py" }],
  },
  {
    text: "If your party splits up, press ⚙️ Settings, then Game clock, then Rests & time…, then ✂️ Split the party, and name two or three groups. Each group gets its own clock, and the buttons act on the group you pick. Players can see the group names. While the party is split, DMbot won't move a clock from what it hears. To bring everyone back, press 🤝 Rejoin the party and pick the time to keep. Undo puts it back.",
    names: [
      { name: "Split the party", file: "dm_screen/clock.py" },
      { name: "Rejoin the party", file: "dm_screen/clock.py" },
      { name: "Undo", file: "dm_screen/clock.py" },
    ],
  },
  {
    text: "The clock tells you on your DM screen when dawn, noon and dusk go by. It also says so once when 24 game hours pass without a long rest (an optional rule). It only tells you. You decide what it means for the story.",
  },
  {
    text: "To time a spell or effect, press Start a timer on the clock message, or press Time it on a spell's rules card. The clock lists up to five timers, soonest first, and tells you once when one has probably ended. DMbot never starts a timer by itself.",
    names: [
      { name: "Start a timer", file: "dm_screen/clock.py" },
      { name: "Time it", file: "dm_screen/rules_cards.py" },
    ],
  },
  {
    text: "After a game, press 🧑 Check who is who. DMbot notices when someone answers to a name several times (\"Mike, what do you do?\" then \"I run with my sword\"). You see who it thinks plays whom and say yes or no. Nothing is used until you say yes.",
    names: [{ name: "Check who is who", file: "ui/people_review.py" }],
  },
  {
    text: "During a game, DMbot may ask \"Did they mean…?\" when it isn't sure it heard a name right. If nobody answers, DMbot keeps the question for you. After the session, your DM screen says how many are waiting. Press ❓ Check names (only you can press it). For each one, pick a name, press Type it…, Keep \"the word\", or press ⏳ Later. Your answer fixes that one line of the cleaned-up transcript, not the raw one. DMbot writes the name that way from then on. Questions you leave for a week go away on their own. Nothing changes until you answer.",
    names: [
      { name: "❓ Check names", file: "ui/heard_review.py" },
      { name: "Type it…", file: "transcript/questions.py" },
      { name: "⏳ Later", file: "ui/heard_review.py" },
    ],
  },
  {
    text: "To keep your homebrew in a text file, press ⚙️ Settings, then 📚 Homebrew, then 📤 Download file. To load a file back, press 📥 Load file. An entry that came from a book you shared gets a note line under its name: [Shared book], and also [Legacy 2014] if the book is for older rules than your table plays. The note is only for you to read. Loading the file ignores it. Entries you load are your own homebrew unless you confirm \"I have the right\" for a book.",
    names: [
      { name: "Homebrew", file: "dm_screen/settings.py" },
      { name: "📤 Download file", file: "ui/homebrew.py" },
      { name: "📥 Load file", file: "ui/homebrew.py" },
    ],
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
    text: "You can choose how long a campaign keeps its transcripts: Forever, 1 year, 90 days or 30 days (⚙️ Settings → 📜 Transcripts). Older ones are deleted within a day. Recaps stay. Each campaign's Transcripts page on this website shows its limit, so you can save a copy before a transcript is deleted.",
    names: [{ name: "Transcripts", file: "dm_screen/transcript_keep.py" }],
  },
  {
    text: "To add homebrew (your own spells, monsters, items and other game content), press ⚙️ Settings, then 📚 Homebrew. Press ➕ Add to type an entry in, or 📥 Load file to bring in a text file (see keeping homebrew in a text file). When you load a file, DMbot asks Who made these? Pick Me, A website or Someone else, and the name stays with the entries. It also asks whether it is from a book. Made-up rules go in House rules, not here.",
    names: [
      { name: "➕ Add", file: "ui/homebrew.py" },
      { name: "📥 Load file", file: "ui/homebrew.py" },
      { name: "Who made these?", file: "ui/homebrew.py" },
      { name: "A website", file: "ui/homebrew.py" },
      { name: "Someone else", file: "ui/homebrew.py" },
    ],
  },
  {
    text: "To keep an entry but stop DMbot using it, open it and press Turn off. An entry that is off is kept, and DMbot ignores it. Press Turn on to use it again. Everyone in your server can see which entries are off on this website.",
    names: [
      { name: "Turn off", file: "ui/homebrew.py" },
      { name: "Turn on", file: "ui/homebrew.py" },
    ],
  },
  ...(HOMEBREW_LINKS_LIVE ? homebrewLinkSteps : []),
  ...(STORY_THREADS_LIVE ? storyThreadSteps : []),
  ...(STANDING_LIVE ? standingSteps : []),
  ...(HOMEBREW_KINDS_LIVE ? homebrewKindSteps : []),
  ...(SECRET_CLUES_LIVE ? secretClueSteps : []),
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
    text: "Only the game master whose plan a campaign uses can delete it, and not while it is being played. Press ⚙️ Settings, then 🗑 Delete this campaign. DMbot lists what will be lost: story memory, notes, recaps, house rules, homebrew and transcripts. To keep a copy first, press 💾 Back up first. Then press Delete it and type the campaign's name. It can't be undone. Copies people already downloaded stay, and the transcript and chat channels stay until a server admin removes them.",
    names: [
      { name: "Delete this campaign", file: "dm_screen/delete_campaign.py" },
      { name: "Back up first", file: "dm_screen/delete_campaign.py" },
      { name: "Delete it", file: "dm_screen/delete_campaign.py" },
    ],
  },
  {
    text: "To see or change who your game masters are, press ⚙️ Settings, then 👑 Game masters. To add one, press ➕ Add a game master and pick someone from your server (people only, not bots). A campaign can have up to 5. They get a private message, and right away they can see the DM screen and change settings. To remove one, press ➖ Remove a game master, pick them, then press Remove once to confirm. They lose access right away, even in the middle of a game. You can remove yourself too. You can't remove the last game master, or the one whose plan the campaign uses; hand the campaign over first (⚙️ Settings, then Hand over). Being made a game master doesn't record anyone. Being recorded is still each person's own yes.",
    names: [
      { name: "Game masters", file: "dm_screen/game_masters.py" },
      { name: "Add a game master", file: "dm_screen/game_masters.py" },
      { name: "Remove a game master", file: "dm_screen/game_masters.py" },
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
    text: "Your table's house rules are on this website too. Sign in, open My Account, and read them there. You can save them as a text file. Rules your table follows from another table are listed too. Only your game master can change them, in Discord.",
    link: { text: "Read your table's house rules", href: "/account/house-rules" },
  },
  {
    text: "The homebrew your game master shares (extra monsters, spells and items made for your game) is on this website too. Sign in, open My Account, and read it there. Entries from a shared book say From a shared book. If that book is for older rules than your table plays, they also say [Legacy 2014]. Homebrew your table follows from another table is listed too, but only what that table shares. You can save your table's own homebrew as a text file. You can't change it here. Your game master does that in Discord. Rules your table made up are under House rules, not Homebrew.",
    link: { text: "Read your table's homebrew", href: "/account/homebrew" },
  },
  {
    text: "You can also read transcripts on this website. Sign in, open My Account, and pick a game. Choose Cleaned, or As heard, which has every word and spoilers too. You can save a game as a text file. A game whose transcript was deleted still shows, marked as deleted. Each campaign shows how long your table keeps transcripts. Only people who said yes were recorded. To delete your own lines, see Delete my transcripts below.",
    link: { text: "Read your game transcripts", href: "/account/transcripts" },
  },
  {
    text: "To look up a rule, type /dmbot rule and a few words, in any of the campaign's channels. Only you see the answer. It comes from the free rules, your table's house rules, and the homebrew your game master turned on. Entries from a shared book are tagged [Shared book], and [Shared book] [Legacy 2014] if that book is for older rules than your table plays. Each person can ask once every 3 seconds. The answer ends with \"Your DM decides what applies at your table.\"",
    names: [{ name: "/dmbot rule", file: "ui/rule_lookup.py" }],
  },
  {
    text: "Want your own lines gone? In Discord, open the ⚙️ Menu DMbot sends you and press 🗑 Delete my transcripts. It deletes every line you said in that server, from every past game. It can't be undone. Other people's lines, your game master's story notes and copies already downloaded stay.",
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
  // Only when the build has Stripe's billing login link (#1711).
  ...portalLoginSteps(portalLoginUrl()),
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
