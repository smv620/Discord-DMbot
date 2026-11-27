// The words of the Questions page (#3438), the same set as Discord's "❓ Questions for you" (#3435; docs/PLAN.md,
// "❓ Questions for you"). Counts and campaign names only: nothing here ever holds a question's own words.
// Each kind the bot counts has one entry: what the DM calls it, and where it can be answered today. `how` is
// "link" (the page that answers it), or "discord" (not on the website yet: its count and how to answer it there).
// A kind the page does not know yet is shown by its key's count with the Discord line, never dropped.

// "hidden": counted by the bot, but not shown on this page at all (no kind uses it now; kept for the next one).
export type QuestionHow = "here" | "link" | "discord" | "hidden";

export interface QuestionKindWords {
  /** What the DM calls it, as a plain heading. */
  label: string;
  how: QuestionHow;
  /** For `link`: where it is answered, as a path from the campaign's address, and the words of the link. */
  to?: "recaps" | "graph" | "names" | "maps" | "houserules";
  link?: string;
  /** For `discord`: the menu path to this kind's own place in Discord, when it is not the general "Questions for you" (#5259). */
  where?: string;
  /** A second link for a kind answered on two pages (#4875): the Shared rules page lists every campaign, so it takes no ref. */
  also?: { to: "sharing"; link: string };
}

export const questionsPage = {
  heading: "Questions for you",
  loading: "Loading…",
  intro: "DMbot needs your answers on these, campaign by campaign. Check everything takes you through them in order: names, then what was said, then the connections.",
  // One walk through everything that can be answered here (#4193), as Discord's ✅ Check everything.
  checkEverything: (total: number): string => `✅ Check everything (${total})`,
  carryOn: (left: number): string => `✅ Carry on (${left} left)`,
  stopForNow: "Stop for now",
  nextPart: (label: string, count: number): string => `Next: ${label} (${count})`,
  finishWalk: "Done",
  partLine: (label: string, part: number, parts: number, count: number): string => `${label}, part ${part} of ${parts} · ${count} waiting`,
  partDone: (done: string, next: string, count: number): string => `${done} done. Next: ${next} (${count}).`,
  walkStopped: "Stopped. Your answers are kept.",
  walkEnded: "That's everything you can answer here for now. 🎉",
  // Said once, under the list; each Discord-only kind then needs only the short line.
  discordWhere: "Some questions are answered in Discord: open ⚙️ Manage DMbot, then 📬 Questions for you.",
  nothing: "Nothing waiting for you. 🎉",
  showHere: "Answer them here",
  hideHere: "Hide them",
  sessionOf: (date: string): string => `Session of ${date}`,
  // The names no recap has linked, from the lore or the story reader (#5186): listed after the sessions.
  otherNames: "Other new names",
  // Connections, "Which one?" and facts to check are one list on the Story Graph: it opens under the first of them.
  answeredWith: (label: string): string => `Answered with ${label}, in the same list.`,
  noneHere: "Nothing is waiting here now.",
  // The count is true but the list has no recap to open for it (#5186): a name from the lore or the story reader, in Discord for now.
  outsideRecap: (n: number): string => `${n === 1 ? "1 name waits" : `${n} names wait`} outside a recap. Answer ${n === 1 ? "it" : "them"} in Discord with ❓ Questions for you.`,
  // The counts are written by the bot every minute or so: older than five minutes, the page says it is catching up.
  catchingUp: "DMbot is catching up. These numbers may be a little old.",
  notYet: "This page isn't available yet.",
  none: "You don't run a campaign yet, so DMbot has no questions for you.",
  failed: "We couldn't load your questions. Reload the page to try again.",
  discord: "Answer in Discord: ⚙️ Manage DMbot → 📬 Questions for you.",
  discordAt: (path: string): string => `Answer in Discord: ${path}.`,
  campaignLine: (total: number): string => `${total} ${total === 1 ? "question" : "questions"} waiting`,
  kindLine: (label: string, count: number): string => `${label} · ${count}`,
  // Said where a kind's line was when its last item has been answered here (#5356).
  kindDone: (label: string): string => `${label}: all answered. 🎉`,
  // When the open list holds fewer of a kind than the bot counted (#5356): the rest are in the lists below it (suggested names,
  // same-person offers, family links, hand-overs), so the numbers add up.
  kindLineSplit: (label: string, count: number, here: number): string => `${label} · ${count} (${here} here, ${count - here} in the lists below)`,
  badge: (total: number): string => `${total} ${total === 1 ? "question" : "questions"} waiting`,
  linkLabel: "Questions for you",
  kinds: {
    // (answered on this page when it can read them, else the link to the recaps: see `howFor` in Questions.tsx)
    names: { label: "New names to check", how: "here", to: "recaps", link: "Open the recaps" },
    // An imported session's names past the scan's first 10, waiting behind 📝 More names (#5055 B):
    // added from Discord until the website's button (MORE_NAMES_LIVE, #5081, which shows it inside the line).
    more_names: { label: "More names from your sessions", how: "discord" },
    heard: { label: "Did they mean…?", how: "discord" },
    // The website has no look-alikes cards yet (#5259): the Names page cannot answer them, so no link; Discord's own panel does (🔗 Look-alikes).
    lookalikes: { label: "Names that look alike", how: "discord", where: "⚙️ Manage DMbot → 📇 Names → 🔗 Look-alikes" },
    connections: { label: "Connections waiting", how: "here", to: "graph", link: "Open the Story Graph" },
    which: { label: "Which one?", how: "here", to: "graph", link: "Open the Story Graph" },
    // Answered in the Story Graph's waiting list ("Which one?" card for a kept fact, #3463) and in Discord.
    family: { label: "Family ties", how: "here", to: "graph", link: "Open the Story Graph" },
    // A lineage or class the lore gives a character the DM has (#3926): asked in Discord only until the website's card.
    traits: { label: "Character details", how: "discord" },
    // DMbot's guesses about places and languages (#4285): checked in Discord until the website's page (slice 1c).
    // With its switch on, the page links to the Names page (#4841 A); the switch decides, see `howFor` in Questions.tsx.
    world_notes: { label: "World notes", how: "discord", to: "names", link: "Open the Names page" },
    // Story threads the lore holds, to keep or turn down (#4453): in Discord until the website's card.
    // (#4841 A: a link to the Names page while its switch is on.)
    threads: { label: "Story threads to check", how: "discord", to: "names", link: "Open the Names page" },
    // What the players know that sounded like a secret (#4524): answered in Discord until the website's card.
    secrets: { label: "Known or secret?", how: "discord" },
    // Characters who joined the party (#4537 slice 2b): answered in Discord for now; the website half is #4778.
    party_joins: { label: "New in the party", how: "discord" },
    // A goodbye or a death: still in the party? (#4537 slice 6): answered in Discord until the website's half.
    party_leaves: { label: "Still in the party?", how: "discord" },
    // Deeds the party did that mattered to someone, waiting to be recorded (#3591): answered in Discord until the website's card.
    deeds: { label: "Deeds to record", how: "discord" },
    // Continuity heads-ups waiting for the DM (#5147): answered in Discord until the Names page's panel (#5136) links from here.
    heads_ups: { label: "Heads-ups", how: "discord" },
    // What the characters learned, waiting to be kept (#3863): answered in Discord until the website's card (step 2).
    reveals: { label: "Things the characters learned", how: "discord" },
    // A map a player kept, waiting for a DM's yes (#4344): asked in Discord until the Maps page (#4347).
    // (#4841 A: a link to the Maps page while its switch is on.)
    maps: { label: "Maps waiting for your yes", how: "discord", to: "maps", link: "Open the Maps page" },
    claims: { label: "Facts to check", how: "here", to: "graph", link: "Open the Story Graph" },
    readings: { label: "What people believe", how: "here", to: "graph", link: "Open the Story Graph" },
    plot: { label: "Plot check", how: "link", to: "recaps", link: "Open the recaps" },
    recap_approve: { label: "Recaps to approve", how: "link", to: "recaps", link: "Open the recaps" },
    // A newer version of a followed house-rules set (#4845): a link to the House rules page while its switch is on.
    rules: {
      label: "House rule updates",
      how: "discord",
      to: "houserules",
      link: "Open the House rules page",
      also: { to: "sharing", link: "Open the Shared rules page" },
    },
  } as Record<string, QuestionKindWords>,
  // A kind the bot counts and this page has no words for yet.
  otherKind: { label: "Other questions", how: "discord" } as QuestionKindWords,
} as const;

/** The words for a kind's key (an unknown key gets `otherKind`, so a count is never dropped). */
export function kindWords(kind: string): QuestionKindWords {
  return Object.hasOwn(questionsPage.kinds, kind) ? (questionsPage.kinds[kind] as QuestionKindWords) : questionsPage.otherKind;
}
