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
    what: "DMbot listens and sends the game master a private tip, with where it came from. It never makes up the story. The game master decides.",
    example: "Someone casts a spell and the DM sees the rule, and its source, on the DM screen.",
  },
  {
    title: "Only people who said yes",
    what: "Nobody is recorded until they agree, and anyone can stop at any time.",
    example: "Press ⚙️ Menu in DMbot's private message, then Stop recording me.",
  },
  {
    title: "Notes anyone can read",
    what: "Everyone in your Discord server can read and download what was written down.",
    example: "Missed last week? Read the notes before the game starts.",
  },
  {
    title: "Say \"Hey DMbot\"",
    what: "The game master asks a quick question out loud and gets a short, private answer.",
    example: "\"Hey DMbot, how long does Bless last?\" The answer says how sure DMbot is.",
  },
  {
    title: "The game clock",
    what: "Players see the story's day and time in their own channel. The DM sees the exact time.",
    example: "Players see \"Day 5, morning\". The DM can time a spell like Bless.",
  },
  {
    title: "Remembers your story",
    what: "The names, places and characters your table makes up are kept from one game to the next.",
    example: "The innkeeper from last month is still the innkeeper.",
  },
  {
    title: "Character sheets",
    what: "Players link their public online sheet, and DMbot listens for their spells and items.",
    example: "A player says \"I cast shield\" and DMbot already knows their spell list.",
  },
  {
    title: "Backups",
    what: "A paid campaign is never lost if its game master leaves. Anyone in the server can download a backup.",
    example: "A new game master loads the backup and carries on.",
  },
];
