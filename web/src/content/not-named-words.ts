// The words of "Not named yet" on the Names page (#2803): someone the story described and never named ("Ken's sister").
// The game master gives them a name; if DMbot already knows someone with that name it asks whether they are the same
// person, and a name just given can be undone. Plain words. DMbot never decides: the game master does, and every step says
// what it will do before it does it. Counts and stages only in the wait; never a word of a transcript.
export const notNamedWords = {
  group: "Not named yet",
  hint: "The story described this person but never gave a name. Give them one, and DMbot will use it from now on.",
  nameLabel: "Their name",
  give: "Give them a name",
  nameFirst: "Type a name first.",
  open: (who: string): string => `Give ${who} a name`,
  // DMbot knows someone with that name: the game master decides whether they are the same person.
  known: (who: string, other: string): string => `DMbot already knows someone called ${other}. Is ${who} the same person?`,
  sameYes: "Yes, the same person",
  sameNo: "No, a different person",
  sameNoNote: "Pick another name for them, or merge the two later if you change your mind.",
  named: (who: string, name: string): string => `${who} is now called ${name}.`,
  joined: (who: string, name: string): string => `${who} is ${name}. DMbot joined the two.`,
  undone: "Put back the way it was.",
  undo: "Undo",
  // The wait (owner rule, 2026-10-23): what DMbot is doing, how long, and when it last moved.
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    working: "DMbot is changing the name.",
    undoing: "DMbot is putting it back.",
  } as Record<string, string>,
  quiet: "DMbot hasn't answered for a while. It may still do it: check the list before you try again.",
  problems: {
    ambiguous: "Two people could be the one you mean, so DMbot changed nothing. Open the one you mean from the list and try again.",
    gone: "That person has already been named, or is gone. The list is up to date now.",
    "name-exists": "A name like that already exists. Pick another name.",
    "bad-name": "Type a name using letters, numbers or common marks.",
    "ticket-too-long": "That name is too long. Use 100 letters or fewer.",
    "not-allowed": "Only this campaign's DM can name people.",
    "not-found": "That person is gone. The list is up to date now.",
    "too-soon": "DMbot is busy with other changes. Wait a moment, then try again.",
    "still-waiting": "DMbot hasn't finished that yet. It may still happen; the list will show it. Check before trying again.",
    network: "We couldn't reach DMbot, so nothing was changed. Check your connection and try again.",
    server: "That didn't work. Try again in a minute.",
  } as Record<string, string>,
} as const;
