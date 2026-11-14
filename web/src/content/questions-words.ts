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
  to?: "recaps" | "graph" | "names";
  link?: string;
}

export const questionsPage = {
  heading: "Questions for you",
  loading: "Loading…",
  intro: "DMbot is waiting on these answers, campaign by campaign. The oldest question comes first.",
  // Said once, under the list; each Discord-only kind then needs only the short line.
  discordWhere: "Some questions are answered in Discord: open ⚙️ Manage DMbot, then 📬 Questions for you.",
  nothing: "Nothing waiting for you. 🎉",
  showHere: "Answer them here",
  hideHere: "Hide them",
  sessionOf: (date: string): string => `Session of ${date}`,
  // Connections, "Which one?" and facts to check are one list on the Story Graph: it opens under the first of them.
  answeredWith: (label: string): string => `Answered with ${label}, in the same list.`,
  noneHere: "Nothing is waiting here now.",
  // The counts are written by the bot every minute or so: older than five minutes, the page says it is catching up.
  catchingUp: "DMbot is catching up. These numbers may be a little old.",
  notYet: "This page isn't available yet.",
  none: "You don't run a campaign yet, so DMbot has no questions for you.",
  failed: "We couldn't load your questions. Reload the page to try again.",
  discord: "Answer in Discord: ⚙️ Manage DMbot → 📬 Questions for you.",
  campaignLine: (total: number): string => `${total} ${total === 1 ? "question" : "questions"} waiting`,
  kindLine: (label: string, count: number): string => `${label} · ${count}`,
  badge: (total: number): string => `${total} ${total === 1 ? "question" : "questions"} waiting`,
  linkLabel: "Questions for you",
  kinds: {
    // (answered on this page when it can read them, else the link to the recaps: see `howFor` in Questions.tsx)
    names: { label: "New names to check", how: "here", to: "recaps", link: "Open the recaps" },
    heard: { label: "Did they mean…?", how: "discord" },
    lookalikes: { label: "Names that look alike", how: "link", to: "names", link: "Open the Names page" },
    connections: { label: "Connections waiting", how: "here", to: "graph", link: "Open the Story Graph" },
    which: { label: "Which one?", how: "here", to: "graph", link: "Open the Story Graph" },
    // Answered in the Story Graph's waiting list ("Which one?" card for a kept fact, #3463) and in Discord.
    family: { label: "Family ties", how: "here", to: "graph", link: "Open the Story Graph" },
    // A lineage or class the lore gives a character the DM has (#3926): asked in Discord only until the website's card.
    traits: { label: "Lineage and class", how: "discord" },
    // A map a player kept, waiting for a DM's yes (#4344): asked in Discord until the Maps page (#4347).
    maps: { label: "Maps waiting for your yes", how: "discord" },
    claims: { label: "Facts to check", how: "here", to: "graph", link: "Open the Story Graph" },
    readings: { label: "What people believe", how: "here", to: "graph", link: "Open the Story Graph" },
    plot: { label: "Plot check", how: "link", to: "recaps", link: "Open the recaps" },
    recap_approve: { label: "Recaps to approve", how: "link", to: "recaps", link: "Open the recaps" },
    rules: { label: "House rule updates", how: "discord" },
  } as Record<string, QuestionKindWords>,
  // A kind the bot counts and this page has no words for yet.
  otherKind: { label: "Other questions", how: "discord" } as QuestionKindWords,
} as const;

/** The words for a kind's key (an unknown key gets `otherKind`, so a count is never dropped). */
export function kindWords(kind: string): QuestionKindWords {
  return Object.hasOwn(questionsPage.kinds, kind) ? (questionsPage.kinds[kind] as QuestionKindWords) : questionsPage.otherKind;
}
