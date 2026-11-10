// The words of the lineage and class question (#3946), the same as Discord's 🧬 card (core/src/dmbot/ui/trait_questions.py):
// the question first, then where the lore said it, then why DMbot asks. The quote is the DM's own lore: the page shows it to the
// DM alone (the door sends it to no one else). Plain words; a name or a quote is only ever text on the page.
import type { TraitOffer } from "../account/api";

export const traitWords = {
  heading: "Lineage and class",
  loading: "Loading…",
  failed: "We couldn't load these. Reload the page to try again.",
  why: "DMbot never changes a character without asking you.",
  yes: "Yes, change it",
  no: "No, leave it",
  gone: "Already answered. Showing what is left.",
  nothingLeft: "Nothing left to answer. If a lore upload finds more, it shows here.",
  couldNot: "DMbot couldn't save that answer. Try again in a minute.",
  saving: "Saving your answer",
  waiting: (n: number): string => `${n} waiting`,
  source: (upload: string): string => (upload ? `Your lore "${upload}" says so` : "Your lore says so"),
  noPlayers: "Only you see this question. It never goes to your players.",
  // Said after a yes or a no (the bot's Undo for a yes is on the way).
  kept: (who: string, what: string, lineage: boolean): string => (lineage ? `Done: ${who} is now ${a(what)}.` : `Done: ${what} added to ${who}.`),
  left: (who: string, what: string): string =>
    `Left as it was. DMbot won't ask about ${who} and ${what} again. To change it later, open ${who}'s name card.`,
} as const;

const a = (word: string): string => `${/^[aeiou]/i.test(word) ? "an" : "a"} ${word}`;

/** The question in one plain line, and what DMbot has now (Discord's `card_text`, without the markup). */
export function questionOf(o: TraitOffer): { ask: string; has: string; yes: string; no: string } {
  const what = o.name.toLowerCase();
  if (o.kind === "species") {
    const now = (o.current ?? "").toLowerCase();
    return {
      ask: `Make ${o.who} ${a(what)}?`,
      has: now ? `DMbot has ${o.who} as ${a(now)}.` : `DMbot has no lineage for ${o.who} yet.`,
      yes: now ? `${o.who} becomes ${a(what)} instead of ${a(now)}.` : `${o.who} becomes ${a(what)}.`,
      no: now ? `${o.who} stays ${now}` : "nothing changes",
    };
  }
  const now = o.classes.map((c) => c.toLowerCase()).join(", ");
  return {
    ask: `Add ${what} to ${o.who}'s classes?`,
    has: now ? `DMbot has ${o.who} as ${now}.` : `DMbot has no class for ${o.who} yet.`,
    yes: `${what} is added to ${o.who}'s classes${now ? "; the ones they have stay." : "."}`,
    no: "nothing changes",
  };
}
