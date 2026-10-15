// What's new (#1321): what people can do now, newest first, and only what is live on the server
// (docs/testing-status.log, "LIVE NOW"; docs/testing-history.log has the deploys). Add an entry
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
    title: "Move to a new Discord account",
    what: "To move to a new Discord account, press Get a move code on your account page. On the new account, type /dmbot migrate, press Import my account, and type the emailed code; you will be asked to say yes to being recorded again.",
  },
  {
    date: "2026-10-15",
    title: "Share house rules with other tables",
    what: "A game master on a paid plan can share their house rules with a share code. Another table can use them, keep its own changes, and get updates when the rules change.",
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

const months = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "2026-10-15" as "15 October 2026": fixed words, so it reads the same on every computer. */
export function formatDate(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  return `${day} ${months[(month ?? 1) - 1]} ${year}`;
}
