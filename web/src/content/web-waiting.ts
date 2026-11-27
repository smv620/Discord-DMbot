// The Story Graph's list of what waits for the DM (#2635, #2652): facts DMbot heard at the table,
// connections found in a backstory (grouped by the upload each came from), "Which Mara?" questions and
// "Who is she?" pronoun questions (#2831).
// Plain words; the DM decides everything here, DMbot only asks. Nothing is shown to players: the API
// answers only a DM of the campaign. WEB_WAITING_LIVE moves with the What's new entry "What is waiting
// for you, on the Story Graph"; it needs CloudDev's #2638 and #2654 deployed and dev1 to have seen one fact
// confirmed and one "Which Mara?" answered from the site.
export const WEB_WAITING_LIVE = true;

/** Where "Proposed" differs from "Now" (#4429, the owner, 2026-11-15): the connection phrases are compared word by word, from the front and
 * from the back; what is left in the middle of the proposed one is bold. A wholly new phrase ("ally of" for "knows") is bold whole. The names
 * never differ on this card, so they are not compared. */
export function differs(now: string, proposed: string): { before: string; bold: string; after: string } {
  const a = now.split(" ").filter(Boolean);
  const b = proposed.split(" ").filter(Boolean);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const join = (words: string[]): string => words.join(" ");
  return { before: join(b.slice(0, head)), bold: join(b.slice(head, b.length - tail)), after: join(b.slice(b.length - tail)) };
}

export const waitingWords = {
  // The line under a yes: the bot showed it to the players, or kept it hidden beside another hidden connection (#3974, #3986).
  shownOne: "👁️ Your players can see this now: it was said at the table.",
  heldOne: "🙈 Kept hidden from your players, so it can't give away another hidden connection between these two names. To show it, use “👁️ Reveal to players” on the Story Graph.",
  // The party was split when it was said: the bot kept it for the characters who were there, or could not tell who heard it (#4035).
  sceneOne: "👁️ Only the characters who were there know this: the party was split.",
  splitOne: "🙈 Kept hidden from your players: the party was split, and DMbot can't tell which group heard it. To choose who knows it, use “Who knows this?” on the Story Graph.",
  heading: "Waiting for you",
  count: (n: number): string => (n === 1 ? "1 thing is waiting for you." : `${n} things are waiting for you.`),
  none: "Nothing is waiting for you.",
  intro: "DMbot never decides these. Each one needs your answer before it goes into your story.",
  more: "Showing the first ones. Answer some and the next ones appear.",

  claimsHeading: "Said at the table",
  claimsNote: "DMbot heard these at the table. Say whether each one is true.",
  confirm: "Yes, that's true",
  confirmSecret: "True, but keep it secret",
  reject: "Not right",

  backstoryHeading: "From your backstory",
  backstoryNote: "Connections found in the backstory you uploaded.",
  fromUpload: (label: string): string => `From ${label}`,
  fromEarlier: "From an earlier upload",
  keep: "Keep",
  // A refused companion keep: the store's sentence for why, then what to press (#5348). The sentence is never edited.
  companionRefused: (why: string): string => `DMbot can't keep that one. ${why} Press ❌ Leave it out to move on.`,
  // The connection card asks two questions apart (#4430, words from #4429): Q1 update or add it, Q2 can players see it.
  nowLine: (subject: string, how: string, object: string): string => `Now: ${subject} ${how} ${object}`,
  proposedLine: (subject: string, how: string, object: string, hand: string): string => `Proposed: ${subject} ${how} ${object}${hand}`,
  proposedLabel: "Proposed: ",
  nowLabel: "Now: ",
  guessLine: (guess: string): string => `🤔 DMbot's guess: ${guess} (your lore only hints at it)`,
  // After a Keep all that leaves DMbot's guesses (#3928): Keep all never keeps a guess, so the DM is told how many still wait.
  guessesLeft: (n: number): string =>
    n === 1 ? "1 of DMbot's guesses still needs your yes." : `${n} of DMbot's guesses still need your yes.`,
  fromText: (quote: string): string => `From your text: “${quote}”`,
  // The Players line: how it is now and how it will be after (an update), or how it will be (a new one).
  playersNew: (hidden: boolean): string => (hidden ? "🤫 Hidden from players." : "👁️ Players will see it."),
  playersUpdate: (nowHidden: boolean, hidden: boolean): string =>
    !nowHidden && !hidden
      ? "👁️ Players can see it now, and still will after the update."
      : nowHidden && hidden
        ? "🤫 Hidden from players now, and still will be after the update."
        : !nowHidden
          ? "🤫 Players can see it now. After the update it will be hidden."
          : "👁️ Hidden from players now. After the update they will see it.",
  playersSecretName: "🤫 Hidden from players: it uses a secret name.",
  hideFromPlayers: "🤫 Hide from players",
  letPlayersSee: "👁️ Let players see it",
  updateIt: "✅ Update it",
  addIt: "✅ Add it",
  leaveAsIs: "❌ Leave it as it is",
  leaveOut: "❌ Leave it out",
  nothingChangesUpdate: "Nothing changes until you press ✅ Update it.",
  nothingChangesAdd: "Nothing is added until you press ✅ Add it.",
  keepAllPlayers: "Keep all uses what each card says about players.",
  keepSecret: "Keep as secret",
  keepIsSecret: "Keep (secret)",
  skip: "Skip",
  keepAll: (n: number): string => `Keep all (${n})`,
  keepAllCut: (done: number, left: number): string =>
    `Kept ${done}. ${left} more are ready: press the button again to keep them.`,
  waitingOn: (names: string[]): string => {
    const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : (names[0] ?? "the new name");
    return `Answer ${list} in Check new names first, then this one can be kept.`;
  },
  secretNote: "This one is secret, so it stays secret from players when you keep it.",

  whichHeading: "Same name, which one?",
  whichNote: "A name in your backstory matches more than one entry.",
  whichQuestion: (word: string): string => `Which ${word} is this?`,
  checkLine: (check: string): string => `DMbot's check: ${check}`,
  whichLine: (other: string, how: string, word: string, otherIsSubject: boolean): string =>
    otherIsSubject ? `${other} ${how} ${word}` : `${word} ${how} ${other}`,
  neither: "Neither",
  // A fact kept because a name in it could be two different entries (Family ties, #3463).
  tiesHeading: "Which one?",
  tiesNote: "DMbot found a name that could mean two different entries. You choose which one. DMbot never chooses for you.",
  tiesQuestion: (word: string): string => `Which ${word} does this mean?`,
  tiesWhy: "DMbot isn't sure which one this name means, so it is asking you.",
  tiesNoNote: "No note",
  tiesPick: (name: string, tag: string): string => (tag ? `${name} (${tag})` : name),
  tiesNeither: "None of these",
  tiesNeitherHelp: "Not either one? DMbot forgets this fact. This can't be undone.",
  tiesPickHelp: "Pressing a name only records your choice. Nothing is added to your story until you press Keep.",
  tiesUndo: "Undo my pick",
  tiesUndone: "Your pick is taken back. The fact is under Which one? again.",
  // "Some know" on a waiting claim (#4680 part 1): the DM ticks who knows it; DMbot guesses nobody.
  someButton: "👥 Some know",
  someLegend: "Who knows this?",
  someHelp: "Tick who knows this.",
  someLoading: "Loading…",
  someSave: "Save",
  someCancel: "Cancel",
  someEmpty: "No player characters yet.",
  someDoneOne: (name: string): string => `👤 Known to: ${name}.`,
  someDoneMany: (names: string): string => `👥 Known to: ${names}.`,
  someGone: "That was already answered. Reload to see where it stands.",
  someNotFound: "DMbot could not find one of those characters. Reload and try again.",
  someUndo: "↩ Undo",
  someUndone: "Taken back. The claim is waiting again.",
  // "Some know" on a backstory connection (#4728): held back from the players; there is no website Undo for it.
  someDoneBackstoryOne: (name: string): string => `👤 Known to: ${name}. Held back from your players.`,
  someDoneBackstoryMany: (names: string): string => `👥 Known to: ${names}. Held back from your players.`,
  someBackstoryNote: "Your players won't see this until it is said. If the card replaces a connection they can see now, they won't see that one any more.",
  someBackstoryRefused: "DMbot couldn't keep that one. Check the characters and try again.",
  pronounHeading: "Who is this?",
  pronounNote: "DMbot isn't sure who a word like \"she\" means.",
  pronounQuestion: (word: string): string => `Who is ${word}?`,
  pronounNoName: "No name fits yet.",
  someoneElse: "Someone else",
  someoneElseFor: (word: string): string => `Someone else for ${word}`,
  hiddenMark: "🤫",
  hiddenWord: "Hidden from players:",
  saidAt: (day: string): string => `Said in the session of ${day}.`,
  hiddenNote: "🤫 means players can't see that name.",

  savingStage: "Saving your answer",
  keepingStage: (n: number): string => `Keeping ${n} connections`,
  queuedStage: "Waiting for DMbot",
  keepingConnections: "Keeping connections",
  done: {
    confirm: "Added to your story as true.",
    confirmSecret: "Added to your story as true, and kept secret.",
    reject: "Dropped. DMbot won't ask about it again.",
    keep: "Kept.",
    keepSecret: "Kept as a secret.",
    skip: "Skipped.",
    leftAsIs: "Left as it is.",
    leftOut: "Left out.",
    pick: "Picked. It now shows under From your backstory.",
    neither: "Neither. No connection was made.",
    tiesPick: "Saved your choice. The fact is now in the list below. Press Keep to add it to your story.",
    tiesNeither: "Forgotten. DMbot won't ask about this fact again.",
    someoneElse: "Someone else. DMbot kept the connection with a person who isn't named yet.",
  },
  kept: (n?: number): string =>
    n === undefined ? "Done. The list shows what is left." : n === 0 ? "Nothing was ready to keep." : n === 1 ? "Kept 1 connection." : `Kept ${n} connections.`,

  errors: {
    gone: "That one isn't waiting any more. The list now shows what is.",
    "name-first": "Answer the new name in Check new names first, then try this again.",
    "stopped-recording": "Someone who said it has stopped being recorded, so DMbot dropped it. Nothing to do.",
    "cannot-write": "DMbot can't add that right now. A name in it may have changed. It stays here: press Not right, or try again later.",
    "bad-target": "DMbot can't do that to this one. The list now shows what is there.",
    "queue-full": "Too many connections are waiting. Answer some of them first, then try again.",
    "still-waiting": "DMbot is still working on your last answer. Look again in a moment. It won't be counted twice.",
    "not-found": "That one isn't there any more. The list now shows what is.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    other: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string>,
  loadFailed: "We couldn't read what is waiting. Try again in a minute.",
  reload: "Try again",
};
