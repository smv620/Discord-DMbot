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
    what: "A DM can choose how long a campaign keeps its transcripts: forever, 1 year, 90 days or 30 days (Settings, then Transcripts). Anything older is deleted every night, and recaps stay.",
  },
  {
    date: "2026-10-16",
    title: "Delete my transcripts",
    what: "Press ⚙️ Menu in DMbot's private message to you, then Delete my transcripts, to remove every line you said in that server. It can't be undone, and other people's lines and downloaded copies stay.",
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
    date: "2026-10-17",
    title: "See the sites you approved",
    what: "In Settings, then Homebrew, press Approved sites to see each site you said you may use, who said yes and when. You can take one back, and nothing is deleted.",
    reason:
      "Goes with adding homebrew from a link, which waits for dev1's try on a real page (#1634, #1652).",
  },
  {
    date: "2026-10-16",
    title: "Share imported homebrew in a pack",
    what: "A pack can now include homebrew you added from a link. It travels as a pointer to the page, not a copy, and a table that follows the pack says once, for each site, that it has the right to use it before those entries are used.",
    reason:
      "Goes with adding homebrew from a link, which waits for dev1's try on a real page (#1634, #1635).",
  },
  {
    date: "2026-10-16",
    title: "Add homebrew from a link",
    what: "In Settings, then Homebrew, press Add from a link and paste the address of a published homebrew page. DMbot reads that one page, shows it to you, and saves it only when you press Save. The first time for a site, you say you have the right to use it; after that it asks nothing. You can also say who made a file you load. Approved sites, in the same place, shows each site you said yes to and lets you take one back.",
    reason:
      "Held until dev1 has tried one real published page from the server: the site may block server traffic (#1634).",
  },
  {
    date: "2026-10-16",
    title: "Fix a line",
    what: "If DMbot wrote one of your lines down wrong, type /dmbot fix, or press Fix one of my lines after a session, then pick the line and type what it should say. Your DM says yes or no on the DM screen, and nothing changes unless they say yes. DMs can fix any line themselves: type /dmbot fix or press Fix a line on the DM screen. It changes right away, and you can Undo it while the session runs.",
    reason: "Held until it has been seen working on the test server (#1491, #1499).",
  },
  {
    date: "2026-10-16",
    title: "Recaps for your table",
    what: "After a session, DMbot can write a short story-style recap. You read it first, change it if you like, and press Post to the table. Nothing goes to your players until you do. You can also read, post or skip it right on the DM screen, and the next session start reminds you once if you forgot. If your plan reads recaps aloud, you can tell the narrator how to say a name. On the recap page you choose what recaps do. Paid plans only.",
    reason:
      "Recaps are off until dev1 has checked a real one (#1382). When they are on, move this up and turn on the recap words (RECAPS_LIVE in recap-words.ts).",
  },
  {
    date: "2026-10-16",
    title: "Narrated recaps with music",
    what: "DMbot can read your recap out loud in a narrator's voice, with music behind it, and you can tell it how to say a name. It comes with Guild and Pro, and you can add it to other paid plans.",
    reason:
      "Needs the owner's ElevenLabs account and the first real narrated recap (#1385). When it is on, move this up and turn on RECAP_AUDIO_LIVE in recap-words.ts (it needs RECAPS_LIVE and the add-on's price too).",
  },
  {
    date: "2026-10-16",
    title: "DMbot's helper answers questions",
    what: "Ask the team a question from your account page and you get an email at once to say we have it. DMbot's helper, an AI program and not a person, answers first, a person on the team takes over if it cannot help or you ask, and your account page shows who answered.",
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
    date: "2026-10-16",
    title: "Choose what recaps do",
    what: "In ⚙️ Settings, the game master can choose, for each campaign, whether DMbot writes a recap after a session, whether it makes the narration and music (where the plan includes them), and whether it emails them when it is ready. DMbot never posts one without them.",
    reason:
      "Goes with the recap, which stays off until the first check on a real session (#1382).",
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
    title: "Story threads you keep",
    what: "On the Names list, press New thread to keep a quest, mystery, hook or goal, and say how far along it is. Its card shows who is involved, what moved it, and its history. Say Still going when you check on it. After a session, DMbot tells you which threads haven't moved for a few sessions, so you can say Still going, Resolved or Dropped. When a session starts, the note before it names the quiet ones, with a Threads button for your list. On a clue, event or thread, press Led to to say what it led to, and the cards show where each came from. It only reminds you; you decide. Players never see your threads.",
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
    what: "Each campaign now gets a channel called dmb-dm-bot with one message and two buttons: 👤 My buttons shows the ones for your part, and only you see them. The buttons moved off your DM screen, so it now holds only notes, alerts and Stop listening.",
    reason:
      "Held until it has been seen working on the test server (#1666).",
  },
  {
    date: "2026-10-17",
    title: "A Shared story switch",
    what: "In Settings, press Shared story to choose Off, Watch the story or Guide me. The first time you turn it on, DMbot reminds you to only give it material you have the right to use, and keeps a note that you said so. For now the switch only remembers your choice and does nothing else; reading your adventure comes next.",
    reason:
      "Held until DMbot can read an adventure (#1692): a switch that does nothing yet is not news.",
  },
  {
    date: "2026-10-17",
    title: "Homebrew menu by kind",
    what: "In Settings, then Homebrew, you now see one button for each kind, like Spells 10/12, which means 10 are on out of 12. Press one to see only those entries; ↩ All kinds takes you back.",
    reason:
      "Held until it has been seen working on the test server (#1665).",
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
