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
    date: "2026-10-16",
    title: "Delete my past transcripts",
    what: "You can now remove what DMbot wrote down from you. In DMbot's private message to you, press Menu, then Delete my transcripts (or type /dmbot delete-my-lines in the server). DMbot tells you how many lines it will delete, and you confirm once. Your words are removed from every transcript in that server. Other people's lines, the facts your DM confirmed and recaps stay. Transcripts someone already downloaded are not changed.",
    reason: "Held until it has been seen working on the test server (#1532).",
  },
  {
    date: "2026-10-16",
    title: "Fix a line",
    what: "If DMbot wrote one of your lines down wrong, type /dmbot fix, or press Fix one of my lines after a session, then pick the line and type what it should say. Your DM says yes or no on the DM screen, and nothing changes unless they say yes. DMs can fix any line themselves: type /dmbot fix or press Fix a line on the DM screen. It changes right away, and you can Undo it while the session runs.",
    reason: "Held until it has been seen working on the test server (#1491, #1499).",
  },
  {
    date: "2026-10-16",
    title: "Your own homebrew",
    what: "Your campaign can keep its own spells, monsters, items and rules. Open Settings, then Homebrew, to add them in your own words. Only you see an entry until you let players know. When you look up a rule, your homebrew comes before the free rules. DMbot can also show a short card when your table says one of your homebrew names, and you can download your homebrew as a file or load one back. On a paid plan you can share your homebrew as a pack with another table, and follow packs other DMs share. Your own homebrew always wins, new versions of a pack you follow arrive at your next session start, and you can undo them or pick only the changes you want. If your table made an entry the pack lacks, you can suggest it to the pack's DM without your name.",
    reason: "Held until it has been seen working on the test server (#1520, #1527).",
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
