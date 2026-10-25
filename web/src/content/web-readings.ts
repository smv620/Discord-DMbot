// "What people believe" on the Story graph (#2412 slice 3b; core PR 2693): the theories, accusations, reveals
// and tricks DMbot noticed, which the DM may keep as what someone believes (never as a fact). Plain words;
// the DM decides, DMbot only lists. Only a DM of the campaign gets the list from the API; a player or
// someone peeking behind the DM screen gets nothing and sees nothing. Switched off until CloudDev's door
// (PR 2693) is merged and deployed: WEB_READINGS_LIVE moves with the What's new entry "What people believe,
// on the Story graph".
export const WEB_READINGS_LIVE = false;

export const readingWords = {
  heading: "What people believe",
  lead: "Things DMbot noticed that someone seems to think, or that was shown. Keep one and it is written down as what that person believes, never as a fact. You decide.",
  count: (n: number): string => (n === 1 ? "1 belief is waiting for you." : `${n} beliefs are waiting for you.`),
  none: "No beliefs are waiting for you.",
  more: "Showing the first ones. Answer some and the next ones appear.",
  rules: {
    theory: "A theory",
    accusation: "An accusation",
    reveal: "Something revealed",
    confession: "Something a spell made someone say",
    evasion: "A dodge under a spell",
    false_clue: "A false clue",
    plot: "A secret plot",
  } as Record<string, string>,
  believedBy: (who: string): string => `Believed by ${who}`,
  sure: (how: string): string => `How sure: ${how}`,
  saidAt: (day: string): string => `From the session of ${day}.`,
  place: "Keep as a belief",
  no: "Not right",
  undo: "Undo",
  waitingOn: (names: string[]): string => {
    const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : (names[0] ?? "the new name");
    return `Answer ${list} in Check new names first, then this one can be kept.`;
  },
  // The wait (owner rule 2026-10-23): the stage in words, how long, and when it last moved.
  stages: {
    queued: "Waiting for DMbot to pick it up",
    working: "DMbot is saving it",
  } as Record<string, string>,
  stalledWhat: "DMbot hasn't moved on this for a while. It may still finish; look again in a minute. Nothing is counted twice.",
  done: {
    place: "Kept as a belief.",
    no: "Dropped. DMbot won't ask about it again.",
    undo: "Taken back. It is waiting again.",
  },
  errors: {
    gone: "That one isn't waiting any more. The list now shows what is.",
    "name-first": "Answer the new name in Check new names first, then try this again.",
    "stopped-recording": "Someone who said it has stopped being recorded, so DMbot dropped it. Nothing to do.",
    "cannot-write": "DMbot can't keep that right now. It stays here: press Not right, or try again later.",
    "bad-reading": "DMbot can't keep this one as a belief. The list now shows what is there.",
    "unknown-character": "A character in it isn't in this campaign, so it can't be kept.",
    "waiting-already": "That belief is already waiting for you in the other list.",
    "plot-is-known": "That plot is already known, so there is nothing to keep.",
    "already-known": "That belief is already kept.",
    "queue-full": "Too many answers are waiting. Give DMbot a moment, then try again.",
    "still-waiting": "DMbot is still working on your last answer. Look again in a moment. It won't be counted twice.",
    "not-found": "That one isn't there any more. The list now shows what is.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    other: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string>,
  loadFailed: "We couldn't read what people believe. Try again in a minute.",
  reload: "Try again",
};
