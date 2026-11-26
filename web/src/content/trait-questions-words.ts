// The words of the Character details question (#3946, the language question #4271), the same as Discord's 🧬 card
// (core/src/dmbot/ui/trait_questions.py): the question first, then where the lore said it, then why DMbot asks. The quote is the
// DM's own lore: the page shows it to the DM alone (the door sends it to no one else). Plain words; a name or a quote is only ever
// text on the page.
import type { TraitOffer } from "../account/api";

export type TraitKind = TraitOffer["kind"];

export const traitWords = {
  heading: "Character details",
  languages: "Languages",
  loading: "Loading…",
  failed: "We couldn't load these. Reload the page to try again.",
  why: "DMbot never changes a character without asking you.",
  yes: "Yes, change it",
  yesAdd: "Yes, add it",
  no: "No, leave it",
  // (#4908: answered, maybe in Discord, or removed when its speaker stopped being recorded or its
  // transcript was deleted; true of all)
  gone: "This question isn't waiting any more. Showing what is left.",
  // A refused answer: a language's says the character has a sheet now (as Discord's card does); a lineage or class says
  // they may have been joined with another name or removed.
  refused: (who: string, name: string, kind: TraitKind): string =>
    kind === "language"
      ? `DMbot couldn't add it: ${who} has a sheet now, so add ${name} there. Showing what is left.`
      : `DMbot couldn't change ${who}: they may have been joined with another name or removed. Showing what is left.`,
  notAllowed: "Only a DM of the campaign can answer this.",
  nothingLeft: "Nothing left to answer. If a lore upload finds more, it shows here.",
  couldNot: "DMbot couldn't save that answer. Nothing changed. Try again in a minute.",
  saving: "Saving your answer",
  waiting: (n: number): string => `${n} waiting`,
  // The lore line: a colon when a quote follows, a full stop when none does.
  source: (upload: string, quoted: boolean): string => {
    const head = upload ? `Your lore "${upload}" says so` : "Your lore says so";
    return quoted ? `${head}:` : `${head}.`;
  },
  stillWorking: "DMbot is still working on your answer. Reload in a minute to see where it stands.",
  // Said after a yes (the bot's Undo for it is in Discord).
  kept: (who: string, what: string, kind: TraitKind): string => {
    if (kind === "language") return `Done: ${who} speaks ${what}. Undo is in Discord.`;
    if (kind === "species") return `Done: ${who} is now ${a(what)}. Undo is in Discord.`;
    return `Done: ${what} added to ${who}. Undo is in Discord.`;
  },
  // Said after a no. A language's has no "open the name card" ending.
  left: (who: string, what: string, kind: TraitKind): string =>
    kind === "language"
      ? `Left as it was. DMbot won't ask about ${who} and ${what} again.`
      : `Left as it was. DMbot won't ask about ${who} and ${what} again. To change it later, open ${who}'s name card.`,
} as const;

const a = (word: string): string => `${/^[aeiou]/i.test(word) ? "an" : "a"} ${word}`;

/** The heading of one question's card: the languages have their own; the lineage and class keep the list's. */
export function headingOf(kind: TraitKind): string {
  return kind === "language" ? traitWords.languages : traitWords.heading;
}

/** The name as the page shows it in a sentence: a language keeps its capitals, a lineage or class is lowercased. */
export function nameOf(o: TraitOffer): string {
  return o.kind === "language" ? o.name : o.name.toLowerCase();
}

/** The question in one plain line, and what DMbot has now (Discord's `card_text`, without the markup). */
export function questionOf(o: TraitOffer): { ask: string; has: string; yes: string; no: string; yesWord: string } {
  const what = nameOf(o);
  if (o.kind === "language") {
    return {
      ask: `Does ${o.who} speak ${o.name}?`,
      has: "",
      yes: `${o.name} is added to ${o.who}'s languages.`,
      no: "nothing changes",
      yesWord: traitWords.yesAdd,
    };
  }
  if (o.kind === "species") {
    const now = (o.current ?? "").toLowerCase();
    return {
      ask: `Make ${o.who} ${a(what)}?`,
      has: now ? `DMbot has ${o.who} as ${a(now)}.` : `DMbot has no lineage for ${o.who} yet.`,
      yes: now ? `${o.who} becomes ${a(what)} instead of ${a(now)}.` : `${o.who} becomes ${a(what)}.`,
      no: now ? `${o.who} stays ${now}` : "nothing changes",
      yesWord: traitWords.yes,
    };
  }
  const now = o.classes.map((c) => c.toLowerCase()).join(", ");
  return {
    ask: `Add ${what} to ${o.who}'s classes?`,
    has: now ? `DMbot has ${o.who} as ${now}.` : `DMbot has no class for ${o.who} yet.`,
    yes: `${what} is added to ${o.who}'s classes${now ? "; the ones they have stay." : "."}`,
    no: "nothing changes",
    yesWord: traitWords.yes,
  };
}
