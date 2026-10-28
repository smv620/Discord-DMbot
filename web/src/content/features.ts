// What DMbot does at a table (#1141): only things that are built and merged. Add a card when
// a feature ships, never before. Plain words, one sentence and one example each; no game or
// publisher trademarks (CLAUDE.md), so character sheets are named without the brand.
import { GAMETIME_IN_CHAT_LIVE, INITIATIVE_STYLE_LIVE } from "./help";
import { RECAPS_LIVE } from "./recap-words";

export interface Feature {
  title: string;
  what: string;
  example: string;
}

export const featuresHeading = "What DMbot does at your table";

const cards: (Feature & { live?: boolean })[] = [
  {
    title: "Quiet tips for the DM",
    what: "DMbot listens to the people who said yes and sends the game master a private tip with its source. It never makes up the story. The game master decides.",
    example: "Someone casts a spell. The DM sees the rule and where it comes from, on the DM screen.",
  },
  {
    title: "Only people who said yes",
    what: "Nobody is recorded until they say yes. That covers what you say and what you type in the table's chat. Anyone can stop at any time.",
    example: "To stop, press 🗂️ Menu in DMbot's private message, then Stop recording me.",
  },
  {
    title: "Transcripts anyone can read",
    what: "Everyone in your Discord server can read and download the written record of each game.",
    example: "Missed last week? Read what was said before the game starts.",
  },
  {
    title: 'Ask "Hey DMbot"',
    what: "Anyone at the table who said yes can ask a quick rules question out loud or in the dmb-chat channel. The short answer is posted there, and the game master has the final say. Questions and answers go in the written record.",
    example: '"Hey DMbot, how long does Bless last?" The answer says how sure DMbot is.',
  },
  {
    title: "The game clock",
    what: GAMETIME_IN_CHAT_LIVE
      ? "The game time shows in the dmb-chat channel as a small 🕰️ line whenever it changes. The game master sees the exact time."
      : "Players see the story's day and time in their own channel. The game master sees the exact time.",
    example: 'Players see "Day 5, morning". The game master can time a spell like Bless.',
  },
  {
    title: "Character sheets",
    what: "Players link their public online sheet to their character, and the game masters see it on the character's card.",
    example: "No more asking \"what's on your sheet?\" in the middle of a fight.",
  },
  {
    title: "FightBot reminders in a fight",
    what: `In a fight, FightBot privately reminds each player what their own sheet says they still have; it only reminds, and the game master decides.${INITIATIVE_STYLE_LIVE ? " It times each reminder to how your table runs initiative." : ""}`,
    example: "You still have Hunter's Mark and two level 1 slots.",
  },
  {
    title: "Remembers your story",
    what: "Names, places and characters your game master confirms are kept from one game to the next, along with which items are one of a kind and who carries them. DMbot only points out when something doesn't add up, and never makes up story.",
    example: "The innkeeper from last month is still the innkeeper. You said there is only one magic sword, so DMbot flags it if two people carry it.",
  },
  {
    title: "Recaps after each game",
    what: "After a session, DMbot can write a short story-style recap. You read it first, change it if you like, and press Post to the table. Nothing goes to your players until you do.",
    example: "After Friday's game, fix a misspelled name, then press Post to the table.",
    live: RECAPS_LIVE,
  },
  {
    title: "Your own homebrew",
    what: "Add your own spells, monsters and items, switch each on or off, or add one from a published page's link; DMbot shows what it read and saves only when you press Save.",
    example: "In Settings, open Homebrew, paste a link, check what DMbot read, then press Save.",
  },
  {
    title: "Backups (paid plans)",
    what: "A campaign is never lost if its game master leaves. Anyone in the server can download a backup, which holds the game master's secrets.",
    example: "A new game master loads the backup and carries on.",
  },
  {
    title: "Share house rules (paid plans)",
    what: "A game master on a paid plan can choose to share their house rules with another table using a share code. The other table can use them, keep its own changes, and suggest a rule back. Only the rules are shared, never the story, names or what was said.",
    example: "A friend's table starts with your house rules, then tweaks the ones that don't fit their game.",
  },
];

// Only the cards whose feature is live (a card without a switch is always live).
export const features: Feature[] = cards
  .filter((c) => c.live !== false)
  .map(({ title, what, example }) => ({ title, what, example }));
