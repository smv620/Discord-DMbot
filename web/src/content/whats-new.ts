// What's new (#1321): what people can do now, newest first, and only what is deployed on the
// server (docs/testing-status.log, "LIVE NOW"; docs/testing-history.log has the deploys). Some
// of it is deployed but not yet tried by a person at a table (the emailed link, typed questions);
// dev1's log says which. Add an entry
// when a feature that people can see goes live, never before: "who carries what" waits for the
// deploy of #1277, and "alive or dead" for the extractor (docs/STORY_MEMORY.md, 5a). The date is
// the day it went live. Plain words, a title of six words at most and one or two sentences, with
// no technical terms (CLAUDE.md, "Simple enough for a child") and no game trademarks.
export interface Entry {
  /** The day it went live, as YYYY-MM-DD. */
  date: string;
  title: string;
  what: string;
}

export const whatsNewHeading = "What's new";
export const whatsNewIntro =
  "What you can do with DMbot now, newest first. Only things that are live are here.";

export const entries: Entry[] = [
  {
    date: "2026-10-20",
    title: "Recaps for your table",
    what: "After a session, DMbot can write a short story-style recap that you read and change if you like, on the DM screen or the website, and post to the table when you choose. Nothing goes to your players until you do, and recaps are for paid plans only.",
  },
  {
    date: "2026-10-20",
    title: "Recaps fit your session",
    what: "A recap's length now follows your session: about 200 words for a one-hour game, about 400 for three hours, and about 700 for a game of six hours or more.",
  },
  {
    date: "2026-10-20",
    title: "Recaps stick to what happened",
    what: "Before you see a recap, DMbot checks the sentences against what was said at the table, has it written again once if one doesn't match, cuts any that still don't, and takes out compass directions. The checking can miss things, so you still read the recap first.",
  },
  {
    date: "2026-10-20",
    title: "Choose what recaps do",
    what: "In ⚙️ Settings, the game master can choose, for each campaign, whether DMbot writes a recap after a session, and whether it emails them when it is ready. DMbot never posts one without them.",
  },
  {
    date: "2026-10-20",
    title: "When you say a secret name",
    what: "When you, the game master, say a name you marked secret out loud, DMbot asks you at once whether it is revealed now. Press Yes, players know now to let DMbot use it in answers to players (you can make it secret again from the name's card), or No, keep it secret to leave it alone for this session.",
  },
  {
    date: "2026-10-19",
    title: "My Account puts sign-in first",
    what: "When you are not signed in, My Account now says so and puts Sign in with Discord and the email link at the top. The plans come after, under New to DMbot?.",
  },
  {
    date: "2026-10-19",
    title: "Campaign labels look like labels",
    what: "On My Account, a campaign's Active and You run it marks are now plain words with a small picture, so they no longer look like buttons you can press.",
  },
  {
    date: "2026-10-19",
    title: "A shorter campaigns list",
    what: "On My Account, Your campaigns shows the five you played most recently, with a button for the older ones. A paused campaign, or one with a hand-over waiting, always stays in view.",
  },
  {
    date: "2026-10-18",
    title: "Search and link Help steps",
    what: "The Help page has a search box that shows only the steps with your words in them. Every section and step also has its own link, and a Copy link button beside each section to paste it to a friend.",
  },
  {
    date: "2026-10-17",
    title: "DMbot works on any new server",
    what: "Add DMbot to a new server and /dmbot start is ready to use. If no list pops up when you type it, wait a minute and try again.",
  },
  {
    date: "2026-10-17",
    title: "Add homebrew from a link",
    what: "In Settings, then Homebrew, press Add from a link and paste the address of a published homebrew page. DMbot shows you what it read and saves it only when you press Save; the first time for a site, you say you have the right to use it.",
  },
  {
    date: "2026-10-17",
    title: "Share imported homebrew in a pack",
    what: "A pack can now include homebrew you added from a link. It travels as a pointer to the page, not a copy, and a table that follows the pack says once, for each site, that it has the right to use it before those entries are used.",
  },
  {
    date: "2026-10-17",
    title: "See the sites you approved",
    what: "In Settings, then Homebrew, press Approved sites to see each site you said you may use, who said yes and when. You can take one back, and nothing is deleted.",
  },
  {
    date: "2026-10-16",
    title: "Which rules is a book for?",
    what: "When you load a homebrew file from a book, DMbot asks if the book is for older rules or the rules your table plays. Entries from an older-rules book are tagged [Legacy 2014] when someone looks up a rule.",
  },
  {
    date: "2026-10-16",
    title: "Check names after a session",
    what: "If DMbot isn't sure it heard a name right, it asks the game master \"Did they mean…?\". Questions nobody answered wait for you: after the session, press Check names on your DM screen and pick a name for each.",
  },
  {
    date: "2026-10-16",
    title: "Keep a connection secret",
    what: "On a name's card, press Connect to…, press 🤫 Next one is public so it reads 🤫 Next one is secret, then pick the other name. Players never see that connection, and only a game master can turn Secret on.",
  },
  {
    date: "2026-10-16",
    title: "Homebrew for your campaign",
    what: "Open Settings, then Homebrew, to keep your own spells, monsters and items, and turn an entry on or off for your table. On a paid plan you can also share a pack with other tables and follow theirs (Settings, Shared rules).",
  },
  {
    date: "2026-10-16",
    title: "Copy a pack's entry to edit",
    what: "Look up an entry from a homebrew pack you follow, then press Make it my own. It copies the entry into your campaign's homebrew so you can change it, and the pack stays as it was.",
  },
  {
    date: "2026-10-16",
    title: "Players can look up rules",
    what: "Anyone at the table can type /dmbot rule and a few words to look up a rule, and only they see the answer. It uses the free rules and the house rules and homebrew your DM shared; your DM decides what applies.",
  },
  {
    date: "2026-10-16",
    title: "Keep transcripts for a set time",
    what: "A DM can choose how long a campaign keeps its transcripts: forever, 1 year, 90 days or 30 days (Settings, then Campaign management, then Transcript retention). Anything older is deleted every night, and recaps stay.",
  },
  {
    date: "2026-10-16",
    title: "Delete my transcripts",
    what: "Press 🗂️ Menu in DMbot's private message to you, then Delete my transcripts, to remove every line you said in that server. It can't be undone, and other people's lines and downloaded copies stay.",
  },
  {
    date: "2026-10-16",
    title: "See how long transcripts are kept",
    what: "The website's Transcripts pages now say how long your table keeps its transcripts, so you can save a copy before they are deleted.",
  },
  {
    date: "2026-10-16",
    title: "Deleted transcripts show in the list",
    what: "A session whose transcript was deleted now stays in the website's list, and says when it was deleted.",
  },
  {
    date: "2026-10-16",
    title: "Read shared homebrew on the website",
    what: "Anyone in your Discord server can read the homebrew your DM shared with the table (open My Account, then Homebrew) and save it as a text file.",
  },
  {
    date: "2026-10-16",
    title: "Read house rules on the website",
    what: "Anyone in your Discord server can read the table's house rules on the website (open My Account, then House rules) and save them as a text file. Only your DM can change them, on the DM screen in Discord.",
  },
  {
    date: "2026-10-16",
    title: "Read transcripts on the website",
    what: "Anyone in your Discord server can read a session's transcript on the website, cleaned or as heard, and save it as a text file. Open My Account, then Session transcripts.",
  },
  {
    date: "2026-10-16",
    title: "Your questions to the team",
    what: "After you write to the DMbot team, your account page lists your questions, with the date you sent each one and whether it is waiting, answered or closed.",
  },
  {
    date: "2026-10-16",
    title: "Quick answers use your facts",
    what: "When you ask DMbot a quick question about someone in your campaign, it now uses the facts you confirmed, like who they work for. If it has nothing confirmed, it says so.",
  },
  {
    date: "2026-10-15",
    title: "Check what DMbot noticed",
    what: "If something doesn't add up, like a one-of-a-kind item that two people carry, DMbot asks you about it after the session. It only points it out, and you decide.",
  },
  {
    date: "2026-10-15",
    title: "Fight reminders for players",
    what: "In a fight, FightBot may send you a short private reminder of what your sheet says you still have, like a spell or spell slots. It only reminds you, and your game master decides; you can switch it off under My character sheet.",
  },
  {
    date: "2026-10-15",
    title: "Stop recording me on the website",
    what: "On My Account, each server where DMbot may record you has a Stop recording me button. It shows a short warning first, and one confirmation stops it right away.",
  },
  {
    date: "2026-10-15",
    title: "Sign in with an emailed link",
    what: "On your account page, type your email and we send you a link that signs you in. It works once and stops working after 15 minutes.",
  },
  {
    date: "2026-10-15",
    title: "Your DMbot ID",
    what: "Your account page now shows your DMbot ID, which is your account number and not a password, and your experience points, which count the minutes DMbot has run for you.",
  },
  {
    date: "2026-10-15",
    title: "Share house rules with other tables",
    what: "A game master on a paid plan can share their house rules with a share code. Another table can use them, keep its own changes, and get updates when the rules change, and the kinds of link between people, places and things that your table made can travel with a set if you tick them.",
  },
  {
    date: "2026-10-15",
    title: "Suggest a rule back",
    what: "A table that uses shared rules can send one of its own rules to the game master who shares them. It goes with no name, and nothing changes until that game master adds it.",
  },
  {
    date: "2026-10-15",
    title: "One-of-a-kind items",
    what: "On an item's name card, a game master can press Only one exists, so DMbot knows there is only one. Press Allow more than one to undo it.",
  },
  {
    date: "2026-10-15",
    title: "Ask DMbot by typing",
    what: "Anyone who said yes can type a quick rules question in the table's dmb-chat channel. The short answer is posted there for everyone, with its source, and the game master has the final say.",
  },
  {
    date: "2026-10-13",
    title: "Link your character sheet",
    what: "Press My character sheet to link your public sheet, or to type your character in. Your game master sees it on your character's card.",
  },
  {
    date: "2026-10-13",
    title: "See when your plan ends",
    what: "If you end your plan, My Account shows the day it ends and says you won't be charged again.",
  },
  {
    date: "2026-10-13",
    title: "The game clock",
    what: "The game master keeps the story's day and time in DMbot. Players see it in their own channel, and the game master sees the exact time.",
  },
];

/**
 * Entries that are deployed but not shown yet, each with the reason (Supervisor, #1322: "live"
 * means deployed and not known to fail). Move one into `entries` when its reason is gone.
 */
export interface Held extends Entry {
  reason: string;
}

export const held: Held[] = [
  {
    date: "2026-10-22",
    title: "A page for each campaign",
    what: "On My Campaigns, tap a campaign's name to open its own page. It has links to its recaps, house rules, homebrew, story map, names and transcripts, all in one place. Players see only the pages for the whole table.",
    reason: "In review (#2416); held until the API sends each campaign's address and dev1 deploys it.",
  },
  {
    date: "2026-10-22",
    title: "Open any NPC and keep your own notes on them",
    what: "Every name in the NPC list gets a 📖 Open button. It opens that name's page on the website, with what the table knows and a box for your own notes. Only you can see your notes. Download or delete them any time.",
    reason: "In review (#2266); held until dev1 deploys it.",
  },
  {
    date: "2026-10-22",
    title: "Upload your campaign's backstory from the main menu",
    what: "Upload your campaign's backstory from the main menu: DMbot suggests its names and how they're connected, and you keep what's right.",
    reason: "In review (#2380); held until dev1 deploys it.",
  },
  {
    date: "2026-10-22",
    title: "Recaps are shorter",
    what: "Recaps are shorter, so the game starts sooner: about 150 words for a one-hour game, 300 for three hours, and 450 at most.",
    reason: "In review (#2368); held until dev1 deploys it.",
  },
  {
    date: "2026-10-22",
    title: "All your names on the website",
    what: "When a name list in Discord is too long to show everything, its last choice is now \"All names on the website\". It gives you a private button that opens the whole list.",
    reason: "In review (#2294); held until dev1 deploys it and the website's Names page is live (#2295, WebDev).",
  },
  {
    date: "2026-10-22",
    title: "Keep a name from the players until they learn it",
    what: "When you add a name, 🙈 Hide from players keeps it to yourself: not in the NPC list, answers or recaps. On its card, 👀 Players know it now shows it to everyone when the players find out.",
    reason: "In review (#2321); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "Pick the other name from a list",
    what: "On a name card, Connect to… and Same as… now show a list of your names to pick from instead of asking you to type. A switch puts the closest names first, or sorts them A to Z. As the game master you can also flip a connection between secret and public right there, without removing it.",
    reason: "In review (#2321); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "DMbot asks when two house rules disagree",
    what: "When you add or change a house rule that says something different from another one, DMbot shows you both and asks which should win. You can replace the old one, or keep both and pick the winner. DMbot never picks for you, and it remembers your answer.",
    reason: "In review (#2284); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "Hear and record how a name sounds",
    what: "Beside the box for how a name is said, you can now ask DMbot for a suggestion, say the name out loud and have it turned into letters, and hear the letters read back. Nothing is saved until you save it, and your recording is deleted once it has been turned into letters.",
    reason: "In review (#2317); held until the API (#2316) and the speech recogniser on the server (#2318) are deployed by dev1.",
  },
  {
    date: "2026-10-21",
    title: "See where your house rules came from",
    what: "On the house rules list, 📂 Sources shows where your rules came from: the linked file, each file you uploaded, rules added in Discord, and the set you follow. Pick one to download just its rules as a text file.",
    reason: "In review (#2285); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "See where your homebrew came from",
    what: "On the Homebrew menu, 📂 Sources shows where your homebrew came from: what you typed, each file you uploaded, each website you linked it from, and each pack you follow. Pick one to download just those entries as a text file.",
    reason: "In review (#2285); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "Recaps are checked before you see them",
    what: "Before you see a recap, DMbot checks it against your story so far and rewrites it, up to four times. It looks for things that matter that were left out, lines that don't fit the story, and any reason or plan nobody at the table said. DMbot never says what a character wants unless a player or you said it. If the checks still don't agree, you get the best version with a short list of what is left to check.",
    reason: "In review (#2255); held until dev1 deploys it and the recap page shows the list (criticNotes).",
  },
  {
    date: "2026-10-21",
    title: "Every NPC has its own page",
    what: "Each name in the NPC list gets a 📖 Open button that opens that NPC's page on the website, for anyone in your Discord server. It shows what the list shows and the recaps that mention the name. There is also a My notes box on each page: only you can see your notes, you can download them or delete them, and they never go into a backup.",
    reason: "In review (#2265); held until dev1 deploys it and the website's NPC page is live (WebDev).",
  },
  {
    date: "2026-10-21",
    title: "Recaps say when they wait",
    what: "If DMbot can't finish its last check of a recap right away, the recap page now says it is written and waiting for one last check, instead of saying it is still being written. When the checks weren't sure about something, a closed section called What the checks said lists it for you.",
    reason: "In review (#2297); held until dev1 deploys the recap checks (#2280), and it goes live together with \"Recaps are checked before you see them\".",
  },
  {
    date: "2026-10-21",
    title: "Import reads more transcripts",
    what: "When you bring in older sessions, DMbot now also reads a Google Doc transcript that writes each name in [square brackets], and a caption file (a .vtt or .srt) that says who is speaking. If it still can't read a file, it now says why: the format is one it doesn't know, or the captions don't say who is speaking.",
    reason: "In review (#2302); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "Every page says who you are",
    what: "Every page now starts with a line saying who you're signed in as, with a Sign out button if that's not you. If you're not signed in, it says so and shows a Sign in with Discord button. After you sign in, you land back on the page you were on.",
    reason: "In review (#2301); held until dev1 deploys the web API, which makes 'back to the page you were on' work.",
  },
  {
    date: "2026-10-21",
    title: "Settings has two small menus",
    what: "Settings is now in the DM Bot channel, under DMbot Main Menu. The choices you use least are grouped: Who can see the DM screen, and Campaign management (hand over, pause, how long transcripts are kept, game masters and delete). Each has a Back button.",
    reason: "In review (#2281); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "See and fix every name online",
    what: "From Discord you can open a page with every name DMbot remembers in your campaign. Search them, filter by kind, and if you are the DM, fix a spelling, change what a name is, add other names, make one secret, merge two names or remove one. ↩️ Undo on the name's card in Discord still works.",
    reason: "In review (#2295); held until its API (#2294) is deployed and dev1 has seen it on the test server; the NPC link on each name needs the NPC pages (#2266).",
  },
  {
    date: "2026-10-21",
    title: "Support emails show the whole chat",
    what: "Emails from DMbot support now show the whole chat so far under the new message, with who wrote each part and the date. They stay together as one chat in your email app, and when you reply, only your new words are kept.",
    reason: "In review (#2192); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "DMbot offers how to say a name",
    what: "When your table says a name differently from how it's spelled, or says out loud how it is said (\"it's pronounced Kay-el\"), DMbot offers to teach the recap narrator that sound. Look for the 🔊 Check how to say button in the names panel. It lets you answer yes, no, or type your own. Nothing changes until you answer. It shows only to DMs, and only when spoken recaps are on for your campaign.",
    reason: "In review (#2171); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "Your support questions now show the whole conversation",
    what: "On your account page, press \"Read the conversation\" under one of your questions to read all of it: what you wrote, the answers from DMbot's helper (a program, not a person), the replies you sent by email and the team's replies, with the dates. On our website, only you and the team can read it.",
    reason:
      "Waits for the website's API change (#2193) to deploy, and for the support helper to be on (SUPPORT_AI_LIVE, #1431). When it is live, move this up with the helper's entry.",
  },
  {
    date: "2026-10-21",
    title: "Make the audio again for a recap you already posted",
    what: "If you fix a name's spelling after a recap was posted, the recap page offers Make the audio again. The new audio is used on the recap page and its link. The message already in Discord keeps its old audio. Only the DM sees the button, and audio has to be part of your plan.",
    reason: "In review (#2203); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "Recaps for the older sessions you bring in",
    what: "When you bring in older sessions on the website, you can ask for a recap of each one. DMbot first looks for new names in each session, in date order, then asks you about them on your DM screen the usual way. A recap waits for your answers about its names, like a recap of a live session, and counts toward your monthly recaps. Only you, the DM, can ask for it, and it only uses the words of people who agreed to be recorded.",
    reason: "In review (#2106); held until dev1 deploys it and the import page offers the choice (IMPORT_LIVE).",
  },
  {
    date: "2026-10-21",
    title: "A recap won't wait forever for your name answers",
    what: "If a recap is waiting for your answers about names, DMbot reminds you once after a day, on your DM screen, with 📝 Check new names and Write the recap now. If nobody answers, after three days it writes the recap with the names as it heard them, and says so on your DM screen. Also: pressing Write a recap on a short session now waits for your name answers too.",
    reason: "In review (#2174); held until dev1 deploys it.",
  },
  {
    date: "2026-10-21",
    title: "Say a name your way, from its card",
    what: "On a name's card in Discord, press 🔊 How to say it to type how the recap narrator should say that name. Leave it empty to have it said as written. It matches the field on the website's Names page, and it only shows when spoken recaps are on for your campaign.",
    reason: "In review (#2171); held until dev1 deploys it.",
  },
  {
    date: "2026-10-20",
    title: "Names from your own story",
    what: "In ⚙️ Settings, Names, then Add many, press 📜 From my story. Paste your campaign background, upload it, or send a link, and DMbot suggests the names in it, each with the line it came from. You say yes or no to each one, or yes to a whole group, such as every place, in one press. DMbot does not keep your text, only a short line beside each name until you answer it. This is for your own writing, not a published book.",
    reason: "In review (#2167); held until dev1 deploys it.",
  },
  {
    date: "2026-10-20",
    title: "Link a server you added with a plain link",
    what: "If you added DMbot to your server with a plain invite link, \"Link this server\" on the website now finds it and puts your name on it. DMbot also notes when it is taken out of a server.",
    reason: "In review (#2163); held until dev1 deploys it.",
  },
  {
    date: "2026-10-20",
    title: "See what your plan is doing",
    what: "My Account now shows when your plan renews, ends or changes. If you cancelled but the paid time hasn't run out, Renew keeps your plan, with no charge before the renewal date. Change plan now opens straight on the plans to choose from.",
    reason:
      "Waits for the website's API change (#2165) to deploy and for the owner to check it in Stripe's test mode. When it is live, move this up.",
  },
  {
    date: "2026-10-20",
    title: "Names spelled out at the table",
    what: "When someone at the table spells a name out loud, like B-R-A-N-N-O-C-H after \"how do you spell that?\", DMbot notes it, using only players who agreed to be recorded. After the session it shows up in 📝 Check new names, marked \"spelled out at the table\". If it sounds like a name you already have, you can correct that name's spelling with one button. DMbot only suggests. Nothing changes until you press a button, and the suggestion goes only to you.",
    reason: "In review (#2168); held until dev1 deploys it.",
  },
  {
    date: "2026-10-20",
    title: "Share a recap with a link",
    what: "Press 📜 Share a recap on your DM screen, pick a recap, then copy its link or send it to your players in chat. Anyone with the link can read the recap, no sign-in. You can turn the link off or make a new one on your recap page. A recap you haven't posted is posted when you share it, after the same checks as Post. Only the DM sees the button.",
    reason: "Waiting for review (#2132) and for dev1 to deploy it.",
  },
  {
    date: "2026-10-20",
    title: "A fixed spelling reaches past transcripts",
    what: "When you fix how a name is spelled, DMbot still knows the old spelling, writes the new one in the cleaned transcripts of your past sessions and in your recaps, and tells you how many changed. A recap you approved stays approved, and what was heard is never changed. One Undo puts the name and the transcripts back.",
    reason: "In review (#2175, #2170); held until dev1 deploys it.",
  },
  {
    date: "2026-10-20",
    title: "A note when a spelling changed a posted recap",
    what: "If a fixed spelling changed the text of a recap you already posted, its page on the website now says \"Spelling updated\". It reminds you that the message in Discord still shows the old spelling.",
    reason: "Waits for the spelling change to reach recaps (#2170) and for dev1 to deploy it (#2178).",
  },
  {
    date: "2026-10-20",
    title: "Your recap waits for your name answers",
    what: "If a session leaves name questions for you, DMbot waits to write the recap until you have answered them, so it uses the right names. Your DM screen says how many are left, with 📝 Check new names and Write the recap now if you would rather not wait. Nothing is written, and nothing costs anything, while it waits.",
    reason: "In review (#2169); held until dev1 deploys it.",
  },
  {
    date: "2026-10-20",
    title: "Bring in older sessions",
    what: "On the website, a game master can load text transcripts (or paste a Google Doc link) of sessions DMbot didn't record, such as a Craig session, and have DMbot read them the way it reads a live session, asking you about any new names. You match each name in a file to a member of your server. DMbot keeps a player's lines only if they have already agreed to be recorded; your word that they agreed is not enough. Everyone else's lines are dropped before anything is saved. Imported sessions can be read by everyone in your server, like any transcript. You choose each session's date and order, and see the estimated cost before you start. Paid plans only.",
    reason:
      "Waits for the importer (#2105, #2106) and the Google Doc link support (#2143) to be merged and deployed, and for the page (#2107). When it is live, move this up and turn on IMPORT_LIVE in import-words.ts.",
  },
  {
    date: "2026-10-20",
    title: "Tell DMbot what to fix",
    what: "On a recap's page, type what you'd like changed. DMbot sorts your note into points, you keep the ones you want, and it writes the recap again with them. That uses one Try again, you approve the new text again, and style notes you keep are saved for this campaign's later recaps too (the newest 10 are kept). To remove them, go to ⚙️ Settings → 📜 Recaps → ✍️ Recap style.",
    reason:
      "Waits for the website's new routes (#2113) to deploy and for dev1 to see it work. When it is live, move this up and turn on RECAP_FIX_LIVE in recap-words.ts (it needs RECAPS_LIVE too).",
  },
  {
    date: "2026-10-20",
    title: "Speak a note to DMbot",
    what: "Under Tell DMbot what to fix, press Speak instead and say what to change (up to a minute). DMbot turns it into words in the box and deletes the recording as soon as the words are ready; nothing is kept but the words. You check the words before you press Send. It counts toward your listening time, and DMbot only listens to people who have agreed to be recorded.",
    reason:
      "Waits for the voice routes (#2113 part 2) to deploy, the microphone header to be live, and for dev1 to see a spoken note turned into words. When it is live, move this up and turn on RECAP_VOICE_LIVE in recap-words.ts (it needs RECAP_FIX_LIVE too).",
  },
  {
    date: "2026-10-20",
    title: "A short session asks first",
    what: "If a session is short, under 30 minutes long or with under 30 minutes of game talk, DMbot asks you on your DM screen before writing a recap. Press Write a recap or No recap. If you don't answer, no recap is written. You decide; DMbot only asks.",
    reason: "In review (#2104); held until dev1 deploys it.",
  },
  {
    date: "2026-10-19",
    title: "Stops listening when no one's there",
    what: "DMbot stops listening by itself when nobody has been in the voice channel for a few minutes, or nobody has spoken or typed for a while, so a forgotten game doesn't use up your listening hours. It warns you first on your DM screen, and you can press Keep listening. In ⚙️ Settings you choose how long \"a while\" is: 5, 10, 15 or 30 minutes (10 to start). When it stops, it sends you a private message saying why and where to change it, and tells each person still in the voice channel.",
    reason: "Merged with #2038 (#2041); held until dev1 deploys it.",
  },
  {
    date: "2026-10-19",
    title: "A 📇 Names button",
    what: "Press 📇 Names to see, search and fix the names DMbot remembers. Any after-session check you haven't done yet waits there too. Only game masters see it.",
    reason: "Merged (#1987); held until dev1 deploys it.",
  },
  {
    date: "2026-10-18",
    title: "See what DMbot looked at",
    what: "Under a rules answer on your DM screen, press 📖 What I looked at to see the rules it used, with the book and page number. Press a rule to open it. Only you see it.",
    reason: "Waits for #1957 to deploy.",
  },
  {
    date: "2026-10-18",
    title: "Look up any rule",
    what: "Type /dmbot rule and a word like cover, dash or long rest, a class feature like rage, a magic item like Bag of Holding, a weapon or armor like longsword or plate armor, a tool like Thieves' Tools, gear like a Healer's Kit, or a feat, species or background like Alert, Goliath or Sage to read what the free rules say. If a spell, condition or creature has the same name, you get that first. Older 2014 rules are marked [Legacy 2014]. Game masters can also look up things like traps, poisons, curses and diseases; players are told \"That's one for your DM.\" A house rule on it shows first.",
    reason:
      "Merged (#1890, #1891, #1912, #1913, #1919); #1935 is in review; held until dev1 deploys it.",
  },
  {
    date: "2026-10-19",
    title: "Look up fight difficulty rules",
    what: "Game masters can ask DMbot for the rules on how hard a fight is, such as the XP budget for the party's level. DMbot shows the rule and its source, and you decide how hard the fight is. Everyone can now also look up magic item rules, like attunement and charges.",
    reason: "Merged (#2004, #2006); held until dev1 deploys it.",
  },
  {
    date: "2026-10-17",
    title: "See what's switched on",
    what: "In Settings, press What's switched on to see which of DMbot's extras are turned on for your table. Only the game masters see it.",
    reason:
      "Merged (#1831); held until dev1 deploys it and the owner's live check is recorded in docs/testing-history.log.",
  },
  {
    date: "2026-10-17",
    title: "Find Settings more easily",
    what: "Press Settings on the step where you pick the voice channel, or type /dmbot settings anywhere in your server. It opens privately, for the campaign's game masters.",
    reason:
      "Deployed 2026-10-17 (#1619); held until the owner's live check is recorded in docs/testing-history.log.",
  },
  {
    date: "2026-10-17",
    title: "Split the party",
    what: "If the party splits up, name two or three groups and each gets its own clock. Press Rejoin the party to bring them together, and Undo if you slip.",
    reason:
      "Deployed 2026-10-17 (#1607); held until the owner's live check is recorded in docs/testing-history.log.",
  },
  {
    date: "2026-10-17",
    title: "Delete a whole campaign",
    what: "The game master whose plan a campaign uses can delete it from Settings, after a warning and typing its name. It can't be undone, so there is a Back up first button.",
    reason:
      "Deployed 2026-10-17 (#1608); held until the owner's live check is recorded in docs/testing-history.log.",
  },
  {
    date: "2026-10-17",
    title: "Add or remove game masters",
    what: "In Settings, pick Game masters to add up to five people to a campaign, or take one off. Their access to the DM screen follows at once.",
    reason:
      "Merged (#1600, #1603); held until the owner's live check is recorded in docs/testing-history.log.",
  },
  {
    date: "2026-10-16",
    title: "Fix a line",
    what: "If DMbot wrote one of your lines down wrong, type /dmbot fix, or press Fix one of my lines after a session, then pick the line and type what it should say. Your DM says yes or no on the DM screen, and nothing changes unless they say yes. DMs can fix any line themselves: type /dmbot fix or press Fix a line on the DM screen. It changes right away, and you can Undo it while the session runs.",
    reason: "Held until it has been seen working on the test server (#1491, #1499).",
  },
  {
    date: "2026-10-19",
    title: "New names from your recap",
    what: "When a recap uses a name DMbot didn't know, it asks you to check it, on your DM screen and by private message. Nothing is added to your game until you say so.",
    reason:
      "Held until dev1 deploys #2030 (#2027, #2031). When it is on, move this up and turn on RECAP_NAMES_LIVE in recap-words.ts (it needs RECAPS_LIVE too).",
  },
  {
    date: "2026-10-20",
    title: "Share a recap by a link",
    what: "On a posted recap's page, a game master can choose Make a link to share it. Anyone who has the link can read the recap without signing in, even people outside your Discord server, and they can pass it on. You can turn the link off or make a new one whenever you like; the old link then stops opening the recap, though anyone who already read it may keep a copy. Treat the link like a password.",
    reason:
      "Waits for the link API (#2118) to deploy and for the page (#2119). When it is live, move this up and turn on RECAP_SHARE_LIVE in recap-words.ts (it needs RECAPS_LIVE too).",
  },
  {
    date: "2026-10-20",
    title: "Approve the text before any audio",
    what: "DMbot makes no voice-over or music until you have read the recap and pressed Approve the text, on your DM screen or on the recap page. Then you pick voice-over, background music, both, or text only (no audio), and you see only what your plan includes. If you change the text afterwards, you approve it again. If a mood or pause mark in the text is wrong, DMbot shows what to fix, and nothing is approved until you fix it.",
    reason:
      "Waits for narrated recaps (RECAP_AUDIO_LIVE, #1385) and for #2083 to deploy. When it is on, move this up with that entry (#2084).",
  },
  {
    date: "2026-10-16",
    title: "Narrated recaps with music",
    what: "DMbot can read your recap out loud in a narrator's voice, with music behind it, and you can tell it how to say a name. It comes with Guild and Pro, and you can add it to other paid plans.",
    reason:
      "Needs the owner's ElevenLabs account and the first real narrated recap (#1385). When it is on, move this up and turn on RECAP_AUDIO_LIVE in recap-words.ts (it needs RECAPS_LIVE and the add-on's price too).",
  },
  {
    date: "2026-10-19",
    title: "Play a recap's voice and music together",
    what: "Voice and music now play together from one Play button. Switch either off, or turn it down, to hear the other alone. A new section, Where the music changes, shows tags in the recap text; only you see them, never your players.",
    reason:
      "Waits for narrated recaps (RECAP_AUDIO_LIVE, #1385). When it is on, move this up with that entry (#2017).",
  },
  {
    date: "2026-10-16",
    title: "DMbot's helper answers questions",
    what: "Ask the team a question from your account page and you get an email at once to say we have it. DMbot's helper, an AI program and not a person, answers first, a person on the team takes over if it cannot help or you ask, and your account page shows who answered. Free-access accounts can ask too.",
    reason:
      "The helper answers only once the support helper is deployed and has answered a real question (#1431, #1452). When it is on, move this up and turn on SUPPORT_AI_LIVE in support-words.ts.",
  },
  {
    date: "2026-10-15",
    title: "Ask a question in public",
    what: "Post a question in the Q&A for DMbot. A helper called QAbot, a bot and not a person, answers what it can from the Help page and the site, and passes the rest to the team.",
    reason:
      "QAbot answers only once it is installed and live (the owner's steps on #1341). Until then a post there waits for the team.",
  },
  {
    date: "2026-10-15",
    title: "Move to a new Discord account",
    what: "To move to a new Discord account, press Get a move code on your account page. On the new account, type /dmbot migrate, press Import my account, and type the emailed code; you will be asked to say yes to being recorded again.",
    reason:
      "The move code goes to the payment email, and payments stay off until the Stripe account exists (#1096), so nobody can use it yet.",
  },
  {
    date: "2026-10-15",
    title: "New names from what was said",
    what: "After a session, DMbot can spot a name it does not know yet and ask the game master who or what it is. Anything said about that name waits until the game master has checked it. A name they turn down is forgotten, along with what was said about it.",
    reason:
      "It needs the story extractor, which stays off until its prompt passes the measurement (#1282).",
  },
  {
    date: "2026-10-16",
    title: "A note before tonight's session",
    what: "When a session starts, DMbot can post a short note on the DM screen, for the game master only. It lists last time's recap, what you confirmed about the names heard last time, anything lost or ended, and how many things said at the table wait for your check. It shows nothing until you have confirmed something. Players do not see it.",
    reason:
      "Held until it has been seen working on the test server (#1403).",
  },
  {
    date: "2026-10-16",
    title: "When a player says a secret name",
    what: "If a player says a name you've marked secret, it ends up in the transcript, which anyone in your server can read. After the session DMbot asks you privately, one name at a time (up to five), whether it should stay secret from players or be opened up. Only you can answer, and DMbot never decides for you.",
    reason:
      "Held until it has been seen working on the test server (#1407).",
  },
  {
    date: "2026-10-18",
    title: "Who is alive, who carries what",
    what: "DMbot remembers who is alive or dead and who carries the one-of-a-kind things, from lines you say yes to. If something doesn't fit, such as a dead character speaking, it asks you privately after the session, and you decide.",
    reason:
      "It needs the story extractor, which stays off until its prompt passes the measurement (#1282); held until it has been seen working on the test server (#1284).",
  },
  {
    date: "2026-10-20",
    title: "Heads-up when a fallen character speaks",
    what: "If someone speaks as a character you confirmed has died, DMbot points it out on your DM screen, and nothing changes unless you press a button. It waits a few minutes between heads-ups, stays silent on Quiet, and players see it only if your DM screen lets them.",
    reason:
      "It needs the story extractor, which stays off until its prompt passes the measurement (#1282); held until it has been seen working on the test server (#2068, #2078), and DMBOT_LIVE_HEADS_UP stays off until dev1's live check.",
  },
  {
    date: "2026-10-16",
    title: "Secrets only some people know",
    what: "Mark a clue or secret as something only some characters know, and list who. After a session, DMbot tells you, privately, if someone who isn't on the list spoke of it. You decide whether they really learned it. DMbot never decides, and players never see the list.",
    reason:
      "It needs the story extractor, which stays off until its prompt passes the measurement (#1282); held until it has been seen working on the test server (#1649).",
  },
  {
    date: "2026-10-17",
    title: "How NPCs feel about the party",
    what: "On an NPC's, group's or place's name card, press Standing to see how it feels about each player character and the party: Hostile, Unfriendly, Wary, Friendly or Devoted, with the reasons. You can set it too. It only reminds you; you decide how they act. Players never see it.",
    reason:
      "Held until it has been seen working on the test server (#1655).",
  },
  {
    date: "2026-10-17",
    title: "Record what your players did",
    what: "On a player character's or the party's name card, press Record a deed. Say what happened, pick up to five characters, groups or places it mattered to, and how it landed: helped them, pleased them, annoyed them or hurt them. DMbot then shows it as a reason on their Standing, and it fades after ten sessions. You can undo it, and your own Standing words still win. Players never see it.",
    reason:
      "Held until it has been seen working on the test server (#1732).",
  },
  {
    date: "2026-10-17",
    title: "Story threads you keep",
    what: "On the Names list, press New thread to keep a quest, mystery, hook or goal, and say how far along it is. Its card shows who is involved, what moved it, and its history. Say Still going when you check on it. After a session, DMbot tells you which threads haven't moved for a few sessions, so you can say Still going, Resolved or Dropped. When a session starts, the note before it names the quiet ones, with a Threads button for your list. On a clue, event or thread, press ➡ Led to… to say what it led to, and the cards show where each came from. It only reminds you; you decide. Players never see your threads.",
    reason:
      "Held until it has been seen working on the test server (#1662).",
  },
  {
    date: "2026-10-17",
    title: "Species, subclasses and backgrounds in homebrew",
    what: "When you add homebrew, you can now pick Species, Subclass or Background as the kind. Pages you add from a link are sorted into the right kind too.",
    reason:
      "Held until it has been seen working on the test server (#1663).",
  },
  {
    date: "2026-10-17",
    title: "Keep track of promises made at the table",
    what: "On the Names list, press Add a promise to note something someone swore to do, then say who promised it and who it was promised to. It shows as kept, broken or open, and after a session DMbot tells you which promises haven't moved for a few sessions. Promises you mark Due soon also show in the note before a session. It only reminds you; you decide. Players never see your promises.",
    reason:
      "Held until it has been seen working on the test server (#1674).",
  },
  {
    date: "2026-10-17",
    title: "A DMbot channel for buttons",
    what: "Each campaign now has a channel called dmb-dm-bot. Press 👤 DMbot Main Menu there to see the buttons for your part; only you can see them. Anyone can press 📖 Look up a rule or 📜 Transcripts. Your DM screen now holds only notes, alerts and Stop listening.",
    reason:
      "Held until it has been seen working on the test server (#1666).",
  },
  {
    date: "2026-10-17",
    title: "A Shared story switch",
    what: "In Settings, press Shared story to choose Off, Watch the story or Guide me. The first time you turn it on, DMbot reminds you to only give it material you have the right to use, and keeps a note that you said so. Then you can give DMbot your adventure as a PDF, Word or text file; it keeps the text, not the file, for that campaign only, and not in its backup, so after a restore you give the file again. Press Read it now and DMbot's AI reads the file once and lists its chapters, places and people; that list is not in the backup either, and players never see it. Remove a file any time and DMbot forgets it and what it read. Say which names in your adventure are the same as ones in your game, and with Watch the story on, DMbot tells you privately, once, when your table has gone against something your adventure needs, for example a person it needs to be there who has died. It never says what to do. On Guide me, a What's next? button shows, privately, which chapter your table seems to be in and what your adventure has next; you can say you are in another chapter. You can also just ask, \"Hey DMbot, what's next?\", and the answer comes to you privately.",
    reason:
      "Held until seen working live (#1692, #1714, #1729): the owner has not yet checked it at a table.",
  },
  {
    date: "2026-10-17",
    title: "Guide me: what's next in your adventure",
    what: "With Guide me on, press What's next? and DMbot tells you privately which chapter your table is probably in (a guess from names heard lately), what else the book has there, and the next chapter. Pick my chapter overrides the guess. It only repeats what your book says and never decides for you.",
    reason:
      "Deployed 2026-10-17 (3b75a693, migration 0126, #1734); held until the owner's live check is recorded in docs/testing-history.log (#1731).",
  },
  {
    date: "2026-10-17",
    title: "Watch the story",
    what: "With Watch the story on, DMbot tells you privately, once, when your table breaks something your adventure needs, for example a person it needs to be there has died. 👍 I know clears it. 📋 Book's words shows what the book says. It never says what to do.",
    reason:
      "Deployed 2026-10-17 (3b75a693, migration 0125, #1730); held until the owner's live check is recorded in docs/testing-history.log (#1729).",
  },
  {
    date: "2026-10-17",
    title: "Link names to your adventure",
    what: "After DMbot has read your adventure, open a name's card and press In my adventure… to say who or where it is. Match names on the Shared story card lists names that look the same, and you say which are the same. Nothing is linked without your say-so. Only the DM sees the links. Take off removes one, and Undo that brings it back.",
    reason:
      "Deployed 2026-10-17 (3b75a693, migration 0124, #1724); held until the owner's live check is recorded in docs/testing-history.log (#1726).",
  },
  {
    date: "2026-10-17",
    title: "Homebrew menu by kind",
    what: "In Settings, then Homebrew, you now see one button for each kind, like Spells 10/12, which means 10 are on out of 12. Press one to see only those entries; ↩ All kinds takes you back.",
    reason:
      "Held until it has been seen working on the test server (#1665).",
  },
  {
    date: "2026-10-17",
    title: "A channel for your house rules",
    what: "DMbot makes a channel for your campaign's house rules, with one message for each rule you have saved and a note for each rule set you follow. Players can read it but not post, and who can see it matches your DM screen setting (private, peek or open).",
    reason:
      "Held until seen working live (#1752): the owner has not yet checked it on the test server.",
  },
  {
    date: "2026-10-17",
    title: "A channel listing the people and groups",
    what: "DMbot makes a channel that lists the people and groups it remembers for your campaign, as you confirmed them: their names, other names, the groups they are in and who they are connected to. Players can read it but not post, and it follows your DM screen setting. DMbot only lists what you confirmed. It never shows secrets, plot notes, or what has happened to someone.",
    reason:
      "Held until seen working live (#1761): the owner has not yet checked it on the test server.",
  },
  {
    date: "2026-10-17",
    title: "A time suggestion from your narration",
    what: "When you narrate time passing without saying how long (\"you walk for a while\"), DMbot can suggest a time jump on your DM screen, such as +2 hours. Nothing changes unless you press the button. Press Not now to skip it.",
    reason:
      "Held until it has been seen working on the test server (#1758).",
  },
  {
    date: "2026-10-17",
    title: "FightBot follows your turn order",
    what: "FightBot listens to the people who agreed to be recorded. When it hears things like \"Mira, you're up\", \"players go first\" or \"declare your actions\", it guesses how your table takes turns. Your DM screen shows the guess, and you can change it just by saying it differently. FightBot then sends each player a short reminder just before their turn. It only guesses: you decide the order, and it never changes your fight.",
    reason:
      "Held until dev1 has live-checked it on the test server (#1771).",
  },
  {
    date: "2026-10-17",
    title: "Manage your plan without Discord",
    what: "If you can't sign in with Discord, use the email on your receipts. Stripe sends you a code, and you can cancel, update your card or get invoices.",
    reason:
      "Held until the billing login link is set in the website's build (PUBLIC_STRIPE_PORTAL_LOGIN_URL, #1711); move it to the news when the link shows.",
  },
  {
    date: "2026-10-17",
    title: "A quiet reminder of the game time",
    what: "If the game clock hasn't come up for a while during a session, DMbot drops one quiet line on the DM screen: \"Where we are: Day 4, afternoon (14:30)\". With a split party it lists each group. It stays quiet during a fight and when the DM screen is set to Quiet. Players never see it.",
    reason:
      "Held until it has been seen working on the test server (#1738).",
  },
  {
    date: "2026-10-21",
    title: "See how your names are connected",
    what: "On the website, pick any name in a campaign and see the names around it as a map, with lines for how they are connected. Choose how far out to look, tap a name to put it in the middle, and tap a line to read its connections. A campaign's game masters can change the words of a connection, make it secret, remove it or add one. Everyone else never sees the secret ones.",
    reason: "Waits for the web's routes (#2323) to be merged and deployed. When it is live, move this up.",
  },
];

const months = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "2026-10-15" as "15 October 2026": fixed words, so it reads the same on every computer. */
export function formatDate(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  const name = months[(month ?? 0) - 1];
  if (name === undefined || !year || !day) throw new Error(`Not a date: ${date}`);
  return `${day} ${name} ${year}`;
}
