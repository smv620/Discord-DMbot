// The Help page (#1142): how to use DMbot in Discord, step by step. Every button and command
// name is copied from what the bot shows, and web/test/help.test.ts checks each one still
// appears in core/src/dmbot, so the page can't drift. Only built features; no game trademarks
// (CLAUDE.md), so the character-sheet steps name no company.
import { QA_URL } from "./account";
import { IMPORT_LIVE, importSteps } from "./import-words";
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

// The note before each session (#1403; core memory/presession.py) and the question about a secret
// name a player said (#1407; core ui/secret_aloud.py) are built and waiting for the owner's live
// checks, so their steps are held with the What's new entries "A note before tonight's session"
// and "When a player says a secret name" (whats-new.ts, `held`). Set each switch to true in the
// same change that moves its entry out of `held`; a test keeps each pair in step. The note's
// story-thread lines and buttons sit behind STORY_THREADS_LIVE and the guide switch, so the step
// names none of them.
export const PRESESSION_NOTE_LIVE = false;
export const SECRET_SAID_LIVE = false;

// The question DMbot asks at once when the game master says a secret name out loud (#2088, #2094;
// core ui/secret_reveal.py). It is on by default on the server (DMBOT_SECRET_REVEAL_ASK), so it
// goes live with the deploy. The step is held with the What's new entry "When you say a secret
// name"; set the switch to true in the same change that moves the entry out of `held`.
export const SECRET_REVEAL_LIVE = true;

export const secretRevealSteps: Step[] = [
  {
    text: "When you say a name you marked secret out loud at the table, DMbot asks you at once on your DM screen whether it is revealed now. If your players can look at your DM screen, it is sent to you as a private message instead. Press Yes, players know now, and DMbot may use the name in answers to players; you can make it secret again from the name's card (in the names list). If DMbot has the name more than once, your answer covers every copy. Press No, keep it secret, and DMbot won't ask about that name again this session. It only asks when you say the name, never when a player does, and it never decides for you. Only the campaign's DMs can answer.",
    names: [
      { name: "Yes, players know now", file: "ui/secret_reveal.py" },
      { name: "No, keep it secret", file: "ui/secret_reveal.py" },
    ],
  },
];

export const presessionNoteSteps: Step[] = [
  {
    text: "When a session starts, if your plan includes story memory, DMbot can post a short note on your DM screen: read it before you begin. It has last time's recap, what you confirmed about the names heard last time, who or what is dead, lost, destroyed, missing or captured, and how many things said at the table wait for your check. If there is nothing to say, there is no note. It comes once a session, not again if DMbot restarts mid-game. DMbot never sends it to players, and it is not in the transcript.",
  },
];

export const secretSaidSteps: Step[] = [
  {
    text: "After a session, DMbot asks you privately about each secret name a player said, one at a time and five at most. A name you marked secret is now in the transcript, which anyone in your server can read. Press Keep it secret and DMbot still will not use it in answers to players, and asks again if it is said in a later session. Press Not secret any more and DMbot may use it in answers to players; you can make it secret again from the name's card. If DMbot has the name more than once, your answer covers every copy. If more than five were said, DMbot asks about the rest next session. Only the campaign's DMs can answer, and DMbot never decides for you.",
    names: [
      { name: "Keep it secret", file: "ui/secret_aloud.py" },
      { name: "Not secret any more", file: "ui/secret_aloud.py" },
    ],
  },
];

// Who is alive or dead and who carries the one-of-a-kind things (#1284; core memory/states.py,
// ui/claims_review.py). The facts come from lines the game master says yes to after a session,
// and those lines come from the story extractor, which stays off until its prompt passes the
// measurement (#1282). So the steps are held with the What's new entry "Who is alive, who
// carries what". Set this to true in the same change that moves that entry out of `held`; a
// test keeps the two, the privacy line and these steps in step.
export const STORY_STATES_LIVE = false;

export const storyStateSteps: Step[] = [
  {
    text: "DMbot remembers who is alive or dead, what has been destroyed, and who carries the one-of-a-kind things. It learns this from what you say yes to after a session (press 🗣️ Check what was said) and, for who carries what, from connections you make on a name's card.",
    names: [{ name: "🗣️ Check what was said", file: "ui/claims_review.py" }],
  },
  {
    text: "To say an item is the only one, type /dmbot names, press 🔍 Find a name, open the item's card and press ☝️ Only one exists. DMbot guesses that a named item, like a magic sword, is the only one. If that is wrong, press Allow more than one on its card.",
    names: [
      { name: "☝️ Only one exists", file: "ui/name_card.py" },
      { name: "Allow more than one", file: "ui/name_card.py" },
    ],
  },
  {
    text: "When something doesn't fit, for example a character you marked dead who speaks again, or one sword in two hands, DMbot asks you privately after the session. Only you see this. Players never do, and it is never in a transcript. DMbot only points it out. You decide: fix the fact or leave it.",
  },
];

// The DMbot channel (#1666, #1676) moves the buttons off the DM screen, so every Help line that
// sent people to ⚙️ Settings "on the DM screen" changes with it. It is held with the What's new
// entry "A DMbot channel for buttons" (whats-new.ts, `held`) until it has been seen on the test
// server. Set this to true in the same change that moves that entry out of `held`; a test keeps
// the two in step and checks that no line points both ways.
export const DM_BOT_CHANNEL_LIVE = false;

// The 📇 Names button (#1986, #1987, core dm_screen/names_button.py) opens the same private names
// card as /dmbot names. It is merged but not deployed, so its What's new entry "A 📇 Names button"
// (whats-new.ts, `held`) is held and Help follows. Set this to true in the same change that moves
// that entry out of `held`; a test keeps the two in step. Where the button is says "the DMbot
// channel" only while that is live (DM_BOT_CHANNEL_LIVE), so no line points both ways.
export const NAMES_BUTTON_LIVE = false;

export const namesButtonSteps: Step[] = [
  {
    text: `${
      DM_BOT_CHANNEL_LIVE
        ? "To see every name DMbot knows in your campaign, press 📇 Names on your card in the DMbot channel, or in ⚙️ Settings."
        : "To see every name DMbot knows in your campaign, press ⚙️ Settings, then 📇 Names."
    } You can search the names and fix one. Any after-session check you haven't done yet (Check names, Check who is who, Check what was said) waits there. You can also type /dmbot names.`,
    names: [
      { name: "Names", file: "dm_screen/names_button.py" },
      { name: "Check names", file: "ui/names.py" },
      { name: "Check who is who", file: "ui/names.py" },
      { name: "Check what was said", file: "ui/names.py" },
      { name: "/dmbot names", file: "bot.py" },
    ],
  },
];

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
    text: "Each campaign has a channel called dmb-dm-bot. Everyone can read it, and only DMbot posts there. It holds one message with two buttons. Press 👤 DMbot Main Menu to see the buttons for your part at the table. Only you see them. Press ❓ Help to read about DMbot.",
    names: [
      { name: "👤 DMbot Main Menu", file: "dm_screen/dm_bot_panel.py" },
      { name: "❓ Help", file: "dm_screen/dm_bot_panel.py" },
    ],
  },
  {
    text: "Everyone also gets 📖 Look up a rule and 📜 Transcripts. If you said yes to being recorded, you get 🗂️ Menu, where you can stop being recorded. Players also get 📜 My character sheet. Game masters get ⚙️ Settings too.",
    names: [
      { name: "🗂️ Menu", file: "consent_dm.py" },
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
    text: "To change the game clock, house rules, or who can see the DM screen, press 👤 DMbot Main Menu in the DMbot channel, then ⚙️ Settings. Who can see the DM screen is under 👁️ Who can see the DM screen.",
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

// Shared story and giving DMbot your adventure (#1692, #1698) are built, but nothing reads the
// adventure yet, so its What's new line "A Shared story switch" is held until it is useful, and Help
// follows (#1720). Set this to true in the same change that moves that entry out of `held`; a test
// ties the two.
export const SHARED_STORY_LIVE = false;

export const sharedStorySteps: Step[] = [
  {
    text: "To choose how DMbot uses your adventure book, press ⚙️ Settings, then 🧭 Shared story. Off means DMbot remembers your table's story only. Watch the story also tells you when your table and the book no longer fit. Guide me adds a button that says what the book has next. Both need you to give it your adventure first, and link names (the steps below).",
    names: [
      { name: "Shared story", file: "dm_screen/story_mode.py" },
      { name: "Watch the story", file: "dm_screen/story_mode.py" },
      { name: "Guide me", file: "dm_screen/story_mode.py" },
    ],
  },
  {
    text: "The first time you turn it on, DMbot says: \"Only give DMbot material you have the right to use. DMbot doesn't check this.\" Press \"I have the right\" to go on, or \"Not now\" to leave it off. DMbot keeps a note of who said so, and when.",
    names: [
      { name: "Only give DMbot material you have the right to use. DMbot doesn't check this.", file: "campaigns/models.py" },
      { name: "I have the right", file: "dm_screen/story_mode.py" },
      { name: "Not now", file: "dm_screen/story_mode.py" },
    ],
  },
  {
    text: "To give DMbot your adventure, press 📖 Give your adventure on the Shared story card, then Give a file. It takes a PDF, Word (.docx) or text (.txt, .md) file up to 10 MB. DMbot keeps the text, not the file, for this campaign only. Players can't open it. Press 🗑 Remove to make DMbot forget one.",
    names: [
      { name: "Give your adventure", file: "dm_screen/adventure_card.py" },
      { name: "Give a file", file: "dm_screen/adventure_card.py" },
      { name: "Remove", file: "dm_screen/adventure_card.py" },
    ],
  },
  {
    text: "To make DMbot read your adventure, press 📖 Read it now on its card, then Yes, read it. DMbot's AI reads one file once and lists its chapters, places and people. It takes a few minutes and you can keep playing. When it's done, DMbot posts a line on the DM screen. 🔄 Refresh shows how far it has got, and 📋 See what was read shows the list, which players never see. It reads one file at a time, so press Read it now again for the next. If a read stops, the card says why and what to press.",
    names: [
      { name: "Read it now", file: "dm_screen/adventure_card.py" },
      { name: "Yes, read it", file: "dm_screen/adventure_card.py" },
      { name: "See what was read", file: "dm_screen/adventure_card.py" },
      { name: "Refresh", file: "dm_screen/adventure_card.py" },
    ],
  },
  {
    text: "You need Shared story on and an adventure that DMbot has read (Give your adventure, then Read it now). To say that a name from your table is someone or somewhere in your adventure, open the name's card and press 📖 In my adventure…, then pick them from the list. The card then shows In your adventure. In each file of your adventure, a name links to one entry, and an entry to one name. A person can only match a person or group, and a place a place. To remove a link, press 📖 In my adventure… again, then Take off. Made a mistake? Press Undo that. DMbot never links names by itself. To be asked, press 🔗 Match names on the Shared story card. DMbot lists up to ten pairs that share a name, without using AI. Pick the ones that are the same in the Same person or place… menu and the ones that are not in the Not the same… menu. A not-the-same answer is remembered, and pairs you skip come back next time. Only the DM sees the links; players never do.",
    names: [
      { name: "In my adventure…", file: "ui/name_card.py" },
      { name: "Take off", file: "ui/book_link.py" },
      { name: "Undo that", file: "ui/book_link.py" },
      { name: "Match names", file: "dm_screen/adventure_card.py" },
      { name: "Same person or place…", file: "ui/book_link.py" },
      { name: "Not the same…", file: "ui/book_link.py" },
    ],
  },
  {
    text: "With Watch the story on, DMbot tells you, privately on the DM screen, when your table no longer fits your adventure. For example, the book needs someone alive who has died at your table. It checks after each session and when you confirm a change to someone's state. It needs the name linked to your adventure first (📖 In my adventure…). Each one is told once, five at most in a message. Press 👍 I know to clear it, or 📋 Book's words to see what the book says. It never tells you what to do; that is your call.",
    names: [
      { name: "I know", file: "ui/story_watch.py" },
      { name: "Book's words", file: "ui/story_watch.py" },
    ],
  },
  {
    text: "With Guide me on, press 🧭 What's next? on the Shared story card, or on the note before a session. If DMbot says it hasn't read your book, press Read it now first. DMbot shows you privately which chapter your table is probably in, what else the book has in that chapter, and the book's next chapter, or says This is the book's last chapter. It guesses from the people and places your table named in the last two sessions that you matched to your adventure. If none are matched yet, it says it has no guess. Not right? Use the Pick my chapter… menu under the answer, and DMbot uses yours. A long book has ◀ Earlier chapters and Later chapters ▶, and with several files DMbot asks which one first. Use DMbot's guess goes back, and ↩ Undo that undoes your pick. Only the DM can use it; everyone else is told so. It uses no AI and only says what your book says. It never decides what happens.",
    names: [
      { name: "What's next?", file: "ui/guide.py" },
      { name: "Read it now", file: "dm_screen/adventure_card.py" },
      { name: "Pick my chapter…", file: "ui/guide.py" },
      { name: "Earlier chapters", file: "ui/guide.py" },
      { name: "Later chapters", file: "ui/guide.py" },
      { name: "Use DMbot's guess", file: "ui/guide.py" },
      { name: "Undo that", file: "ui/guide.py" },
      { name: "This is the book's last chapter.", file: "ui/guide.py" },
    ],
  },
  {
    text: "You can also ask Guide me by saying or typing \"Hey DMbot, what's next?\" or \"Hey DMbot, where are we in the book?\". If you have agreed to be recorded, DMbot hears it. The answer comes to you alone, in your private chat with DMbot. Your question goes into the transcript, but the answer does not: the transcript only notes that DMbot answered privately. Only the DM can ask this. If Guide me is off, DMbot answers as usual.",
  },
  {
    text: "A backup of the campaign keeps only your adventure's name, not its text or what DMbot read, because anyone in your server can download a backup. After you restore, give DMbot the file again.",
  },
];

// Deeds (#1732, #1754) and the "where are we" note (#1738, #1754) are built, but each stays out of
// Help until its held What's new entry ("Record what your players did", "A quiet reminder of the
// game time") has been seen working on the test server. Set a switch to true in the same change that
// moves its entry out of `held`; a test ties each to it.
export const DEEDS_LIVE = false;

export const deedSteps: Step[] = [
  {
    text: "To note something a player character or the party did, open their name card and press ⚔️ Record a deed. Only the DM can. Type what happened, pick up to five NPCs, groups or places it mattered to, then pick how it landed: Helped them, Pleased them, Annoyed them or Hurt them. Press ✅ Record it, or No, cancel. If you have no NPCs, groups or places yet, add one under Names first.",
    names: [
      { name: "⚔️ Record a deed", file: "ui/deeds.py" },
      { name: "Helped them", file: "memory/standing.py" },
      { name: "Pleased them", file: "memory/standing.py" },
      { name: "Annoyed them", file: "memory/standing.py" },
      { name: "Hurt them", file: "memory/standing.py" },
      { name: "✅ Record it", file: "ui/deeds.py" },
      { name: "No, cancel", file: "ui/deeds.py" },
    ],
  },
  {
    text: "DMbot records only what you type. It never listens for deeds. A deed shows on the Standing screen as a reason, like \"(session 4, helped)\". It counts for less after ten sessions, and a feeling you set yourself always wins. ↩ Undo that works for a short while after you record. DMbot only reminds you; you decide how they act. Players never see deeds, and they are never in a transcript.",
    names: [{ name: "↩ Undo that", file: "ui/deeds.py" }],
  },
];

export const WHERE_ARE_WE_LIVE = false;

export const whereAreWeSteps: Step[] = [
  {
    text: "While DMbot is listening, if you have set the game clock and nobody has shown or moved it for half an hour, DMbot posts one short line on your DM screen saying where the game is. Chatty DM screens get it after 15 minutes, and Quiet ones never. It stays silent during a fight, and with a split party it names each group. Players never see it.",
  },
];

// The rules channel (#1752, #1759) is built, but its What's new entry ("A channel for your house
// rules") is held until it has been seen working on the test server. Set this to true in the same
// change that moves that entry out of `held`; a test ties the two. It also carries the sentence that
// a peek now opens this channel too, which is only true once the channel exists.
export const RULES_CHANNEL_LIVE = false;

// The NPC list channel (#1781; #1761, merged in #1779). Off until the owner has seen the channel on the
// test server; flip it in the same change that moves "A channel listing the people and groups" out of
// `held` in whats-new.ts, a test ties the two. It also carries the sentence that a peek opens this channel.
export const NPCS_CHANNEL_LIVE = false;

export const rulesChannelSteps: Step[] = [
  {
    text: "Each campaign also gets a channel called dmb-rules, with a short name after it. It shows the house rules you have saved, one message for each rule, and a note for each house-rule set your campaign follows. DMbot keeps it up to date when you add, change or remove a rule.",
    names: [
      { name: "dmb-rules", file: "dm_screen/rules_channel.py" },
    ],
  },
  {
    text: "Only DMbot posts in dmb-rules. Players can't, and neither can you. It never shows rules that are only proposed, your private notes, secret names, or text from a rulebook. Each rule is shown in your own words. If dmb-rules is deleted, or someone changes its name or who can see it, the next /dmbot start puts it right.",
  },
  {
    text: `Who can read dmb-rules follows your DM screen setting. Private: only DMs. Peek (the usual setting): only players who press Peek behind the DM screen. Open: everyone in the server. ${NPCS_CHANNEL_LIVE ? "When a player peeks, they get dmb-rules and dmb-npcs too, and the warning lists both. Hiding the DM screen again closes all three." : "When a player peeks, they get dmb-rules too, and the warning names it. Hiding the DM screen again closes both."}`,
    names: [{ name: "Peek behind the DM screen", file: "dm_screen/messages.py" }],
  },
];


// Time suggestions (#1758, #1765) are built but off on the server until dev1's live check
// (DMBOT_TIME_SUGGESTIONS), so their What's new entry ("A time suggestion from your narration") is
// held and Help follows. Set this to true in the same change that moves that entry out of `held`; a
// test ties the two.
export const TIME_SUGGESTIONS_LIVE = false;

export const timeSuggestionSteps: Step[] = [
  {
    text: "When you describe time passing without saying how long, like \"you walk for a while\", DMbot may suggest a jump on your DM screen, like \"+2 hours\". Press it to move the clock, press Not now to skip it, or Undo to put it back. Players never see it.",
    names: [{ name: "Not now", file: "dm_screen/time_suggest.py" }],
  },
  {
    text: "It only listens to you, and only if you agreed to recording. It stays quiet during a fight, with a split party, or before the game clock is set, and it won't always notice. It is off unless the person who runs your DMbot turns it on.",
  },
];


export const npcsChannelSteps: Step[] = [
  {
    text: "Each campaign also gets a channel called dmb-npcs, with a short name after it. It lists the people and groups you confirmed, with their other names, groups and who they are connected to. Only DMbot posts there.",
    names: [{ name: "dmb-npcs", file: "dm_screen/npcs_channel.py" }],
  },
  {
    text: "It never shows anything unconfirmed, secret names, your notes, story threads, or whether someone is dead or missing. Only DMbot can post there, not you or the players.",
  },
  {
    text: "Who can read dmb-npcs follows your DM screen setting: private is DMs only, peek is players who press Peek behind the DM screen, open is everyone. If it is deleted or renamed, the next /dmbot start fixes it.",
  },
];

// How the table runs initiative, heard by FightBot (#1788; #1771, merged in #1783). Off until dev1 has
// live-checked it; flip it in the same change that moves "FightBot follows your turn order" out of
// `held` in whats-new.ts, a test ties the two. There is no setting or button for it (the side-initiative
// button went in #1774), so the steps name none.
export const INITIATIVE_STYLE_LIVE = false;

export const initiativeStyleSteps: Step[] = [
  {
    text: "FightBot listens for how your table runs initiative, and times your private reminder to just before you choose. Your game master's words decide. Players only count if two recorded players agree and the game master hasn't said.",
  },
];

// The game master's side of it: players don't see the combat line unless they peek.
export const initiativeStyleDmSteps: Step[] = [
  {
    text: "FightBot listens for how your table runs initiative: one at a time, by side, or rolled each round. Your words decide. Two agreeing players count only if you haven't said. Your DM screen's combat line shows \"Initiative, as heard: ...\".",
  },
  {
    text: "To correct it, say it out loud, like \"we use side initiative\". There is no button. The line says \"as heard last time\" until FightBot hears it again.",
  },
];

// The ℹ️ What's switched on button on the Settings card (#1831, #1833) tells a game master which of
// DMbot's extras are on for the table. It is held with the What's new entry "See what's switched on"
// (whats-new.ts, `held`) until dev1 deploys it and the owner's live check is recorded. Set this to
// true in the same change that moves that entry out of `held`; a test ties the two. The button sits on
// the Settings card, so the step reads the same whether Settings is on the DM screen or the DMbot channel.
export const WHATS_ON_LIVE = false;

export const whatsOnSteps: Step[] = [
  {
    text: "Wondering if something new is on yet? Press ⚙️ Settings, then ℹ️ What's switched on. Only you see the answer, and only game masters can press it.",
    names: [{ name: "What's switched on", file: "features.py" }],
  },
];

// What /dmbot rule finds beyond spells, conditions and creatures (#1924; core ui/rule_lookup.py):
// glossary rules, class features, magic items, feats, species and backgrounds. It is held with the
// What's new entry "Look up any rule" until dev1 deploys it (#1890, #1891, #1912, #1913). Set this
// to true in the same change that moves that entry out of `held`; a test keeps the two in step.
export const RULE_KINDS_LIVE = false;

export const ruleKindSteps: Step[] = [
  {
    text: "/dmbot rule also finds rules like cover or long rest, class features like rage, magic items like Bag of Holding, weapons and armor like longsword or plate armor, tools and gear like Thieves' Tools or a Healer's Kit, and feats, species and backgrounds like Alert, Goliath or Sage. If a spell or condition has the same name, you get that first. If the newer rules have nothing by that name, you get the older 2014 one, marked [Legacy 2014].",
  },
  {
    text: "Some classes share a feature. Type the class first, like rogue evasion, to get that class's. If two things share a name, the list marks them, like Orc (creature). Pick the one you mean.",
  },
  {
    text: "Things for the DM, like traps, poisons, curses and diseases, are not for players. If you ask for one, DMbot says \"That's one for your DM.\"",
  },
];

// Every rule the Help text and the held "Look up any rule" entry name as an example, with the kind it is
// meant to be, so test/rule-examples.test.ts can check each one is in the rules data the bot reads.
// `text` is the exact wording in the sentence; `name` is the entry's name where it differs; `class` is
// for a class feature ("rogue evasion" is the Rogue's Evasion).
export interface RuleExample {
  text: string;
  kind: string;
  name?: string;
  class?: string;
}
export const ruleExamples: RuleExample[] = [
  { text: "cover", kind: "rule" },
  { text: "dash", kind: "rule" },
  { text: "long rest", kind: "rule" },
  { text: "rage", kind: "feature" },
  { text: "rogue evasion", kind: "feature", name: "Evasion", class: "Rogue" },
  { text: "Bag of Holding", kind: "item" },
  { text: "longsword", kind: "weapon" },
  { text: "plate armor", kind: "armor" },
  { text: "Thieves' Tools", kind: "tool" },
  { text: "Healer's Kit", kind: "gear" },
  { text: "Alert", kind: "feat" },
  { text: "Goliath", kind: "species" },
  { text: "Sage", kind: "background" },
  { text: "Orc (creature)", kind: "monster", name: "Orc" },
  { text: "xp budget", kind: "rule", name: "Step 2: Determine Your XP Budget" },
  { text: "attunement", kind: "rule", name: "Attunement" },
  { text: "charges", kind: "rule", name: "Magic Item Charges" },
];

// What the rules index gained on 2026-10-19: how hard a fight is (the SRD's Combat Encounters section,
// for the game master only, #2000) and the Magic Items section (attunement, charges, rarity, value,
// #2001), found by /dmbot rule and the DM sidebar (core rules/index.py, sidebar/context.py). Held with
// the What's new entry "Look up fight difficulty rules" until dev1 deploys it (#2004, #2006). Set this to true
// in the same change that moves that entry out of `held`; a test keeps the two in step.
export const FIGHT_AND_ITEM_RULES_LIVE = false;

// The game master's step: the XP budget is the DM's chapter (`dm_side`), so players get "That's one for
// your DM." DMbot reads the lines for the levels named and does no sum and no ruling (sidebar/context.py
// ENCOUNTER_NOTE), so the words promise neither.
export const fightRuleSteps: Step[] = [
  {
    text: "To check how hard a fight is, ask for the XP budget. Type /dmbot rule, then \"xp budget\", or ask out loud or in chat: \"Hey DMbot, what's the XP budget for level 3 characters?\" DMbot shows the rule for the levels you name, with its source. It doesn't add up the monsters or say if the fight is easy or deadly: that's your call. /dmbot rule answers only you; if a player asks, DMbot says \"That's one for your DM.\"",
  },
];

// For everyone: the Magic Items section is open rules, not the DM's chapter.
export const magicItemRuleSteps: Step[] = [
  {
    text: "You can look up the rules for magic items the same way: type /dmbot rule, then \"attunement\" or \"charges\". You can also ask what rarity means and what an item is worth by rarity.",
  },
];

// The game master sees what players do not (core ui/rule_lookup.py: DM_ONLY, the player branch and the
// typeahead filter). Held with RULE_KINDS_LIVE: it deploys with the same rules data.
export const ruleKindDmSteps: Step[] = [
  {
    text: "/dmbot rule also finds things for you alone, like traps, poisons, curses and diseases. Only you see the answer.",
  },
];

// DMbot stops a session by itself when nobody is there (#2034, #2038): nobody in the voice channel
// for 5 minutes, or nobody has spoken or typed for the campaign's quiet limit (30 minutes, or 60, 90,
// Off). It warns on the DM screen first, with a Keep listening button. The numbers MIRROR core
// idle_stop.py (EMPTY_S, WARN_S) and campaigns/store.py (QUIET_STOP_CHOICES): change them together.
// Held with the What's new entry "Stops listening when no one's there" until dev1 deploys
// #2038. Set this to true in the same change that moves that entry out of `held`; a test keeps the
// two in step.
export const QUIET_STOP_LIVE = false;

export const quietStopSteps: Step[] = [
  {
    text: "If nobody is in the voice channel for 5 minutes, or nobody has spoken or typed at the table for 30 minutes, DMbot warns you on your DM screen, so a forgotten game doesn't use up your listening hours. Press Keep listening, or just speak, type or come back, and it carries on; if nothing changes, it stops listening 5 minutes later. A music bot or any other bot never counts as a person. To change the 30 minutes, use the 🔇 Stop when quiet button in ⚙️ Settings: each press goes to 60, 90 and Off, then back to 30. Off turns off only the quiet stop; the empty-channel stop always stays on.",
    names: [
      { name: "Keep listening", file: "dm_screen/quiet_stop.py" },
      { name: "Stop when quiet", file: "dm_screen/settings.py" },
    ],
  },
];

export const dmSteps: Step[] = [
  { text: "Add DMbot to your Discord server from the Add to Discord page. Send your players to getdmbot.com/players." },
  {
    text: "Type /dmbot start in any text channel. Wait for the list to pop up, pick /dmbot start in it, then send it. Then press ▶ Continue last campaign, or ＋ New campaign if this is your first game.",
    names: [
      { name: "/dmbot start", file: "bot.py" },
      { name: "▶ Continue last campaign", file: "ui/logic.py" },
      { name: "＋ New campaign", file: "ui/dmbot_commands.py" },
    ],
  },
  {
    text: "No list pops up? DMbot's commands can take a minute to show on a new server. Wait a minute, or fully close and reopen Discord, then type /dmbot start again and pick it from the list. If it went out as plain chat, DMbot didn't get it. Delete it and try again.",
  },
  {
    text: "Pick your table's voice channel, then press ▶ Start listening. Each player gets a private message asking if they say yes. Only people who say yes are recorded.",
    names: [{ name: "▶ Start listening", file: "ui/dmbot_commands.py" }],
  },
  ...(DM_BOT_CHANNEL_LIVE ? dmBotChannelSteps : settingsOnDmScreenSteps),
  ...(NAMES_BUTTON_LIVE ? namesButtonSteps : []),
  ...(WHATS_ON_LIVE ? whatsOnSteps : []),
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
  ...(TIME_SUGGESTIONS_LIVE ? timeSuggestionSteps : []),
  ...(WHERE_ARE_WE_LIVE ? whereAreWeSteps : []),
  {
    text: "To time a spell or effect, press Start a timer on the clock message, or press Time it on a spell's rules card. The clock lists up to five timers, soonest first, and tells you once when one has probably ended. DMbot never starts a timer by itself.",
    names: [
      { name: "Start a timer", file: "dm_screen/clock.py" },
      { name: "Time it", file: "dm_screen/rules_cards.py" },
    ],
  },
  ...(INITIATIVE_STYLE_LIVE ? initiativeStyleDmSteps : []),
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
    text: "To keep a connection from your players, open the name, press 🧭 Connect to…, press 🤫 Secret: no, so it reads 🤫 Secret: yes, then pick the other name. Players' cards never show that connection. Only a game master of the campaign can turn Secret on. If a connection is already secret, it stays secret.",
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
  ...(QUIET_STOP_LIVE ? quietStopSteps : []),
  {
    text: "To keep a copy of a campaign, type /dmbot backup (paid campaigns only). The copy also keeps how often names were said and by whom, but after a restore only for people who are still recorded.",
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
    text: "To suggest one of your own house rules to the game master whose rules you use, type /dmbot houserules, press 💡 Suggest on your rule, then press Yes, send it. It goes exactly as written, with no name on it, so check that the rule names no one. Your own rules stay as they are.",
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
    text: "You can choose how long a campaign keeps its transcripts: Forever, 1 year, 90 days or 30 days (⚙️ Settings → 🗂️ Campaign management → 🗄️ Transcript retention). Older ones are deleted within a day. Recaps stay. Each campaign's Transcripts page on this website shows its limit, so you can save a copy before a transcript is deleted.",
    names: [{ name: "Transcript retention", file: "dm_screen/transcript_keep.py" }],
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
  ...(SHARED_STORY_LIVE ? sharedStorySteps : []),
  ...(STANDING_LIVE ? standingSteps : []),
  ...(DEEDS_LIVE ? deedSteps : []),
  ...(RULES_CHANNEL_LIVE ? rulesChannelSteps : []),
  ...(NPCS_CHANNEL_LIVE ? npcsChannelSteps : []),
  ...(HOMEBREW_KINDS_LIVE ? homebrewKindSteps : []),
  ...(SECRET_CLUES_LIVE ? secretClueSteps : []),
  ...(PRESESSION_NOTE_LIVE ? presessionNoteSteps : []),
  ...(SECRET_SAID_LIVE ? secretSaidSteps : []),
  ...(SECRET_REVEAL_LIVE ? secretRevealSteps : []),
  ...(RULE_KINDS_LIVE ? ruleKindDmSteps : []),
  ...(FIGHT_AND_ITEM_RULES_LIVE ? fightRuleSteps : []),
  ...(STORY_STATES_LIVE ? storyStateSteps : []),
  ...(IMPORT_LIVE ? importSteps : []),
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
    text: "Only the game master whose plan a campaign uses can delete it, and not while it is being played. Press ⚙️ Settings, then 🗂️ Campaign management, then 🗑 Delete this campaign. DMbot lists what will be lost: story memory, notes, recaps, house rules, homebrew and transcripts. To keep a copy first, press 💾 Back up first. Then press Delete it and type the campaign's name. It can't be undone. Copies people already downloaded stay, and the transcript and chat channels stay until a server admin removes them.",
    names: [
      { name: "Delete this campaign", file: "dm_screen/delete_campaign.py" },
      { name: "Back up first", file: "dm_screen/delete_campaign.py" },
      { name: "Delete it", file: "dm_screen/delete_campaign.py" },
    ],
  },
  {
    text: "To see or change who your game masters are, press ⚙️ Settings, then 🗂️ Campaign management, then 👑 Game masters. To add one, press ➕ Add a game master and pick someone from your server (people only, not bots). A campaign can have up to 5. They get a private message, and right away they can see the DM screen and change settings. To remove one, press ➖ Remove a game master, pick them, then press Remove to confirm. They lose access right away, even in the middle of a game. You can remove yourself too. You can't remove the last game master, or the one whose plan the campaign uses; hand the campaign over first (⚙️ Settings, then Hand over). Being made a game master doesn't record anyone. Being recorded is still each person's own yes.",
    names: [
      { name: "Game masters", file: "dm_screen/game_masters.py" },
      { name: "Add a game master", file: "dm_screen/game_masters.py" },
      { name: "Remove a game master", file: "dm_screen/game_masters.py" },
    ],
  },
  {
    text: "To give a campaign to another game master, press ⚙️ Settings, then 🗂️ Campaign management, then Hand over. They must say yes.",
    names: [{ name: "Hand over", file: "dm_screen/handover.py" }],
  },
];

// The "What I looked at" button under a rules answer (#1957) is not deployed yet, so its What's new
// entry is held and this Help step follows. Set this to true in the same change that moves that
// entry out of `held`; a test ties the two.
export const SIDEBAR_SOURCES_LIVE = false;

export const sidebarSourceSteps: Step[] = [
  {
    text: "Where did that come from? Under a rules answer, press 📖 What I looked at. You get the source of each rule it used, with the book and page number. Press a rule to open it. Only you see it.",
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
  ...(SIDEBAR_SOURCES_LIVE ? sidebarSourceSteps : []),
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


// Player steps that the /players page (#1821) shows too: one copy each, so the two pages can't drift.
export const consentStep: Step = {
  text: `DMbot sends you a private message. Press ${consent} to say yes.`,
  names: [{ name: "record me", file: "consent_words.py" }],
};

export const noThanksStep: Step = {
  text: "Want to say no? Press No thanks. You can still play.",
  names: [{ name: "No thanks", file: "consent_dm.py" }],
};

export const stopStep: Step = {
  text: "To stop at any time, press 🗂️ Menu in that private message, then Stop recording me, then Yes, stop recording me.",
  names: [
    { name: "Menu", file: "consent_dm.py" },
    { name: "Stop recording me", file: "consent_dm.py" },
    { name: "Yes, stop recording me", file: "consent_dm.py" },
  ],
};

export const transcriptStep: Step = {
  text: "Everyone in the server can read a game's transcript. To read or download one, type /transcript.",
  names: [{ name: "/transcript", file: "ui/transcripts.py" }],
};

export const heyDmBotStep: Step = {
  text: 'Anyone at the table who said yes can ask DMbot a quick rules question: say "Hey DMbot" or type it in the dmb-chat channel. Your question is posted there with your name. Your game master has the final say.',
  names: [{ name: "dmb-chat", file: "dm_screen/dmbchat_channel.py" }],
};

export const fightBotStep: Step = {
  text: "In a fight, DMbot's fight helper, FightBot, may send you a short private reminder of what your sheet says you still have, like a spell, a feature, or spell slots. It only reminds you. Your game master decides what happens. To switch it off, press 📜 My character sheet, then Turn FightBot off.",
  names: [{ name: "Turn FightBot off", file: "ui/sheets.py" }],
};

export const playerSteps: Step[] = [
  consentStep,
  noThanksStep,
  stopStep,
  {
    text: "While DMbot is listening, it also reads what you type in the table's chat, if you said yes. Nothing is read from anyone who said no.",
  },
  heyDmBotStep,
  {
    text: "When you ask DMbot a question in dmb-chat, it shows the name of the character you play once your game master has said yes to it. Until then it shows your server nickname.",
  },
  transcriptStep,
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
  ...(RULE_KINDS_LIVE ? ruleKindSteps : []),
  ...(FIGHT_AND_ITEM_RULES_LIVE ? magicItemRuleSteps : []),
  {
    text: "Want your own lines gone? In Discord, open the 🗂️ Menu DMbot sends you and press 🗑 Delete my transcripts. It deletes every line you said in that server, from every past game. It can't be undone. Other people's lines, your game master's story notes and copies already downloaded stay.",
    names: [{ name: "🗑 Delete my transcripts", file: "consent_dm.py" }],
  },
  {
    text: "Press 📜 My character sheet to link your online sheet, or to type your character in.",
    names: [{ name: "📜 My character sheet", file: "consent_dm.py" }],
  },
  {
    text: "A linked sheet must be set to Public on the sheet's own site, so anyone with the link can open it. Your game master sees it on your character's card.",
  },
  fightBotStep,
  ...(INITIATIVE_STYLE_LIVE ? initiativeStyleSteps : []),
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
    text: "Moving to a new Discord account? Do this for each campaign you own: press ⚙️ Settings, then 🗂️ Campaign management, then press Hand over, and pick your new account or another game master. When all are handed over, cancel your plan on your account page. On the new account, say yes to being recorded again.",
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

// "If something doesn't work" (#1805): the common snags, each checked as true today (the PR names where).
// Every answer is 40 words at most, and a test keeps it so.
export interface Snag extends Step {
  question: string;
}

export const snagsHeading = "If something doesn't work";

const whatsOnSnag: Snag = {
  question: "Something on What's new isn't there for me.",
  text: "Press ℹ️ What's switched on in ⚙️ Settings to see what is on for your table. If something you read about is still missing a day later, ask us in Support.",
  names: [{ name: "What's switched on", file: "features.py" }],
  link: { text: "Getting help", href: "#getting-help" },
};

export const snags: Snag[] = [
  {
    question: "I typed /dmbot start and nothing happened.",
    text: "Pick /dmbot start from the list that pops up, then send it. If no list shows, wait a minute, or fully close and reopen Discord, then try again.",
    names: [{ name: "/dmbot start", file: "bot.py" }],
    link: { text: "The first steps for the game master", href: "#dm-heading" },
  },
  {
    question: "DMbot isn't hearing someone.",
    text: "DMbot only hears people who said yes to recording. They can say yes in DMbot's private message, or type /consent give in your server. Anyone who hasn't said yes is never recorded.",
    names: [{ name: "/consent give", file: "bot.py" }],
  },
  {
    question: "A button says \"This interaction failed\".",
    text: "That is Discord's own message: DMbot didn't answer in time. Wait a few seconds and press the button again. If it keeps happening, tell us.",
    link: { text: "Getting help", href: "#getting-help" },
  },
  {
    question: "DMbot left the voice channel or stopped listening.",
    text: "If DMbot restarts during a game, it rejoins by itself and says so. If the game is over 16 hours old, or it can't rejoin, it says why. Then type /dmbot start and press ▶ Continue last campaign.",
    names: [
      { name: "/dmbot start", file: "bot.py" },
      { name: "▶ Continue last campaign", file: "ui/logic.py" },
    ],
  },
  {
    question: "I can't see the DM screen.",
    text: "Your game master chooses who sees it. Private means only DMs. Peek means players press Peek behind the DM screen (it warns about spoilers). Open means everyone in the server.",
    names: [{ name: "Peek behind the DM screen", file: "dm_screen/messages.py" }],
  },
  {
    question: "My character sheet didn't update.",
    text: "A linked sheet must be set to Public on the site where it lives. After someone says a spell slot was used, DMbot checks the sheet again about a minute later. It never changes your sheet.",
  },
  ...(WHATS_ON_LIVE ? [whatsOnSnag] : []),
];

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

// The page's headings, shared so the "On this page" list (#1811) uses exactly the same words.
export const dmHeading = "For the game master";
export const playerHeading = "For players";
export const accountHeading = "Your account";
export const emailsHeading = "Emails from DMbot";
export const onThisPageHeading = "On this page";
export const backToTop = "Back to top";

/** The "On this page" list: each section that is shown, in page order, linking to its heading.
 * The recaps heading is listed only while recaps are on, as the page itself shows it only then. */
export function onThisPage(recapsLive: boolean, recapsHeading: string): { id: string; label: string }[] {
  return [
    { id: "dm-heading", label: dmHeading },
    { id: "quick-heading", label: quickAnswersHeading },
    ...(recapsLive ? [{ id: "recaps-heading", label: recapsHeading }] : []),
    { id: "player-heading", label: playerHeading },
    { id: "account-heading", label: accountHeading },
    { id: "emails-heading", label: emailsHeading },
    { id: "snags-heading", label: snagsHeading },
    { id: gettingHelp.id, label: gettingHelp.heading },
  ];
}
