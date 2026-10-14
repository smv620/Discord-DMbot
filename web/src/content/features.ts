// What DMbot does at a table (#1141): only things that are built and merged. Add a card when
// a feature ships, never before. Plain words, one sentence and one example each; no game or
// publisher trademarks (CLAUDE.md), so character sheets are named without the brand.
export interface Feature {
  title: string;
  what: string;
  example: string;
}

export const featuresHeading = "What DMbot does at your table";

export const features: Feature[] = [
  {
    title: "Quiet tips for the DM",
    what: "DMbot listens to the people who said yes and sends the game master a private tip with its source. It never makes up the story. The game master decides.",
    example: "Someone casts a spell. The DM sees the rule and where it comes from, on the DM screen.",
  },
  {
    title: "Only people who said yes",
    what: "Nobody is recorded until they say yes. That covers what you say and what you type in the table's chat. Anyone can stop at any time.",
    example: "To stop, press ⚙️ Menu in DMbot's private message, then Stop recording me.",
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
    what: "Players see the story's day and time in their own channel. The game master sees the exact time.",
    example: 'Players see "Day 5, morning". The game master can time a spell like Bless.',
  },
  {
    title: "Remembers your story",
    what: "The names, places and characters your game master confirms are kept from one game to the next.",
    example: "The innkeeper from last month is still the innkeeper.",
  },
  {
    title: "Character sheets",
    what: "Players link their public online sheet to their character, and the game masters see it on the character's card.",
    example: "No more asking \"what's on your sheet?\" in the middle of a fight.",
  },
  {
    title: "Backups (paid plans)",
    what: "A campaign is never lost if its game master leaves. Anyone in the server can download a backup, which holds the game master's secrets.",
    example: "A new game master loads the backup and carries on.",
  },
];
