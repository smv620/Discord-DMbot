import { NAME_LIMIT } from "./names-words";

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
  known: (who: string, other: string): string =>
    `DMbot already knows someone called ${other}. Is ${who} the same person? Yes joins the two into one. No changes nothing.`,
  sameYes: "Yes, the same person",
  sameNo: "No, a different person",
  sameNoNote: (who: string): string => `Okay, nothing changed. Pick a different name for ${who}.`,
  named: (who: string, name: string): string => `${who} is now called ${name}.`,
  joined: (who: string, name: string): string => `${who} and ${name} are now one person.`,
  undone: "Put back the way it was.",
  undo: "Undo the name",
  undoJoin: "Undo the join",
  undoHint: "You can undo this until your next change.",
  // The wait (owner rule, 2026-10-23): what DMbot is doing, how long, and when it last moved.
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    working: "DMbot is changing the name.",
    joining: "DMbot is joining the two.",
    undoing: "DMbot is putting it back.",
  } as Record<string, string>,
  quiet: "No answer yet. It may still go through: look at the list before you try again.",
  problems: {
    ambiguous: "Two people could be the one you mean, so DMbot changed nothing. Open the one you mean from the list and try again.",
    gone: "That person was named or removed already. The list is up to date now.",
    "name-exists": "Someone already has that name. Pick another.",
    "bad-name": "Use letters, numbers, spaces, - or '.",
    "ticket-too-long": `That name is too long. Use ${NAME_LIMIT} letters or fewer.`,
    "not-allowed": "Only this campaign's DM can name people.",
    "not-found": "That person was named or removed already. The list is up to date now.",
    "too-soon": "DMbot is busy with other changes. Wait a moment, then try again.",
    "still-waiting": "No answer yet. It may still go through: look at the list before you try again.",
    network: "We couldn't reach DMbot, so nothing was changed. Check your connection and try again.",
    server: "That didn't work. Check the list, then try again in a minute.",
  } as Record<string, string>,
  // Undo has its own refusals: the naming ones would give the wrong advice.
  undoProblems: {
    gone: "This name was changed since, so DMbot can't undo it. The list shows how it is now.",
    "too-soon": "DMbot is busy with other changes. Wait a moment, then press Undo again.",
    "still-waiting": "DMbot hasn't finished that yet. It may still happen; check the list before you press Undo again.",
    network: "We couldn't reach DMbot, so nothing was changed. Check your connection and press Undo again.",
    server: "Undo didn't work. Check the list, then try again in a minute.",
  } as Record<string, string>,
} as const;
