// The words of World notes on the Names page (#4285): the same as Discord's 🌍 World notes screen (slice 1b, the ux-critic's), so the two
// stay level. DM only. A line's own words (the land, climate and ruler) come from the place screen's words (place-world-words.ts).
import { cultureWords, populationWords } from "./place-culture-lines";
import { placeWorldWords as place } from "./place-world-words";

/** The words for each step, as Discord's card has them: "Mithral Keep is unfriendly toward elves". */
const STEP_WORDS: Record<string, string> = { Hostile: "hostile to", Unfriendly: "unfriendly toward", Wary: "wary of", Friendly: "friendly toward", Devoted: "devoted to" };
const CONDITION_WORDS: Record<string, string> = { open: "Open", hard: "Hard going", closed: "Closed", gone: "Gone" }; // a road's condition (#5003): the labels of Discord's 🚧 menu

export const SHOWN_AT_MOST = 25; // Discord's menu limit; Keep all keeps only the lines the DM was shown

export const worldNotesWords = {
  heading: "🌍 World notes",
  intro: "DMbot's guesses about your world. Only you see these. Keep the ones that are right; press 🚫 Not right on the rest. DMbot treats these as guesses until you check them.",
  loading: "Loading…",
  failed: "Couldn't load your world notes.",
  tryAgain: "Try again",
  none: "Nothing to check right now. New guesses show up here after a session or a lore upload.",
  places: "Places",
  languages: "Languages",
  roads: "Roads",
  feelings: "Feelings toward peoples",
  changeFeeling: "✏️ Change",
  feelingBack: "↩ Back",
  steps: ["Hostile", "Unfriendly", "Wary", "Friendly", "Devoted"] as const,
  feelingLine: (name: string, step: string, target: string): string => `${name} is ${STEP_WORDS[step] ?? step} ${target}`,
  feelingQuestion: (name: string, target: string): string => `How does ${name} feel toward ${target}? One tap saves it.`,
  keepAllFeelings: (n: number): string => (n === 1 ? "✅ Keep the 1 feeling" : `✅ Keep all ${n} feelings`),
  keep: "✅ Keep",
  notRight: "🚫 Not right",
  undo: "↩ Undo",
  keepLabel: (line: string): string => `Keep "${line}"`,
  notRightLabel: (line: string): string => `Not right: "${line}"`,
  keepAllPlaces: (n: number): string => (n === 1 ? "✅ Keep the 1 place" : `✅ Keep all ${n} places`),
  keepAllLanguages: (n: number): string => (n === 1 ? "✅ Keep the 1 language" : `✅ Keep all ${n} languages`),
  keepAllRoads: (n: number): string => (n === 1 ? "✅ Keep the 1 road" : `✅ Keep all ${n} roads`),
  showing: (shown: number, total: number): string => `Showing ${shown} of ${total}. Check some and the rest come up.`,
  /** What a line is and where it came from. */
  kindHint: { terrain: "Land", climate: "Climate", control: "Ruler", condition: "Road condition", culture: "Culture", population: "Who lives here", language: "Language" } as Record<string, string>,
  fromLore: "from your lore",
  fromSession: "from a session",
  kept: (line: string): string => `✅ Kept: ${line}`,
  keptMany: (n: number): string => `✅ Kept ${n} ${n === 1 ? "note" : "notes"}.`,
  takenOut: (line: string): string => `🚫 Taken out: ${line}`,
  notAgain: "DMbot won't guess this again.",
  undone: "↩ Undone.",
  gone: "Someone already checked or changed that note. Here's the list as it is now.",
  nothingKept: "Nothing to keep. Those notes were already checked.",
  tooLate: "Too late to undo. Open the place or the character to change it.",
  notUndone: "Not undone, because something changed since. Open the place or the character.",
  notDm: "Only a DM of this campaign can check world notes.",
  wrong: "Something went wrong. Nothing was changed. Press the button again.",
  working: "Working on it…",
  /** One line's words, as Discord's card says them: the land ("partly" when only part of it), the climate (and dry or wet), who holds
   * the place, or the language. A value of "other" shows the table's own words. */
  place: (n: { name: string; layer: string; value: string; words: string; how: string | null; ruler: string | null; culture?: string | null }): string => {
    // A culture or who lives here (#4412): the words of the place's panel, for the one row (a named culture by its name).
    if (n.layer === "culture") return `${n.name}: ${cultureWords([{ value: n.value, words: n.words, how: n.how ?? "", culture: n.culture ?? null }])}`;
    if (n.layer === "population") return `${n.name}: ${populationWords([{ value: n.value, words: n.words, how: n.how ?? "", culture: n.culture ?? null }])}`;
    if (n.layer === "terrain") {
      const land = n.value === "other" ? n.words : (place.land.choices[n.value]?.[0] ?? n.value);
      return `${n.name}: ${n.how === "partly" ? `Partly ${land.toLowerCase()}` : land}`;
    }
    if (n.layer === "climate") {
      const warmth = n.value === "other" ? n.words : (place.climate.warmth[n.value] ?? n.value);
      return `${n.name}: ${n.how === "dry" || n.how === "wet" ? `${warmth} and ${n.how}` : warmth}`;
    }
    if (n.layer === "condition") {
      // a road's condition (#5003): the labels of Discord's 🚧 menu, the words Discord's list uses: "the road between A and B: Closed"
      const how = n.value === "other" ? n.words : (CONDITION_WORDS[n.value] ?? n.value);
      return `${n.name}: ${how}`;
    }
    return `${n.name}: ${place.ruler.lines[n.how ?? ""] ?? "Held by"} ${n.ruler ?? "a group"}`;
  },
  language: (n: { name: string; language: string }): string => `${n.name}: ${n.language}`,
} as const;
