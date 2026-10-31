// "What people believe" on the Story Graph (#2412 slice 3b; core PR 2693): the theories, accusations, reveals
// and tricks DMbot noticed, which the DM may keep as what someone believes (never as a fact). Plain words;
// the DM decides, DMbot only lists. Only a DM of the campaign gets the list from the API; a player or
// someone peeking behind the DM screen gets nothing and sees nothing. Switched off until CloudDev's door
// (PR 2693) is merged and deployed: WEB_READINGS_LIVE moves with the What's new entry "What people believe,
// on the Story Graph".
export const WEB_READINGS_LIVE = false;

export const readingWords = {
  heading: "What people believe",
  lead: "Things DMbot noticed that someone thinks, or that a spell or a reveal showed. Keep one to note it as what that person believes. It is never a fact. You decide.",
  count: (n: number): string => (n === 1 ? "1 belief to look at." : `${n} beliefs to look at.`),
  none: "Nothing to look at. New ones appear after a session.",
  more: "Showing the first few. Answer some to see more.",
  rules: {
    theory: "A theory",
    accusation: "An accusation",
    lie: "A suspected lie",
    reveal: "Something revealed",
    confession: "Said under a spell",
    evasion: "Avoided an answer under a spell",
    false_clue: "A clue that may be false",
    plot: "A secret plot",
  } as Record<string, string>,
  other: "Something noticed",
  // A suspected lie (#3121): who thinks it and who lied, so the DM sees the name before Keep. The party's own view
  // starts with "The party"; a liar who is gone or unknown is "someone".
  lied: (who: string, liar: string | null): string =>
    `${who === "the party" ? "The party" : who} thinks ${liar ?? "someone"} lied about this`,
  believedBy: (who: string): string => `Believed by ${who}`,
  sure: (how: string): string => `How sure: ${how}`,
  saidAt: (day: string): string => `Session of ${day}`,
  place: "Keep as a belief",
  no: "Drop it",
  undo: "Undo",
  waitingOn: (names: string[]): string => {
    const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : (names[0] ?? "the new name");
    return `Name to check first: ${list}. Open Check new names, answer it, then come back.`;
  },
  // The wait (owner rule 2026-10-23): the stage in words, how long, and when it last moved.
  stages: {
    queued: "In line. DMbot will save it soon",
    working: "DMbot is saving it",
  } as Record<string, string>,
  oneAtATime: "One at a time. The other buttons wake up when this finishes.",
  stalledWhat: "This is taking longer than usual. It may still finish, so check again in a minute. Nothing is saved twice.",
  keptFor: (who: string): string => `Kept: ${who}'s belief.`,
  undoHint: "Undo is here until your next answer.",
  done: {
    no: "Dropped. DMbot won't bring it up again.",
    undo: "Taken back. It is waiting again.",
  },
  errors: {
    gone: "That one isn't waiting any more. The list now shows what is.",
    "undo-gone": "That can't be taken back any more. The list now shows what is.",
    "name-first": "Answer the new name in Check new names first, then try this again.",
    "stopped-recording": "A player asked to stop being recorded, so this was removed. Nothing for you to do.",
    "cannot-write": "DMbot couldn't save that. It is still in the list. Try again in a minute, or press Drop it.",
    "bad-reading": "DMbot can't keep this one. The list has been refreshed.",
    "unknown-character": "It names a character who isn't in this campaign, so it can't be kept. Drop it, or add the character first.",
    "waiting-already": "That belief is already waiting for you in Waiting for you.",
    "plot-is-known": "You already know about this plot, so there is nothing to save.",
    "already-known": "That belief is already kept.",
    "queue-full": "Too many answers are waiting. Give DMbot a moment, then try again.",
    "still-waiting": "DMbot is still working on your last answer. Look again in a moment. It won't be counted twice.",
    "not-found": "That one isn't there any more. The list now shows what is.",
    network: "Couldn't reach DMbot. Check your connection and try again.",
    other: "Something went wrong on our side. Your answer was not saved. Try again in a minute.",
  } as Record<string, string>,
  loadFailed: "Couldn't load the list. Try again in a minute.",
  reload: "Try again",
};
