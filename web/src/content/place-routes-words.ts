// The words of a place's "Next to…" and "Travel to…" on the Names page (#4341): exactly Discord's (core/src/dmbot/ui/place_links.py,
// memory/next_words.py and memory/route_words.py), so the two stay level; core/tests/test_place_routes_words.py fails when they drift.
// Each choice saves its own sentence as the connection's words (the add-link action reads them back as their kind and word). "On a road or
// river" is left out until the add-link action takes a kind and a detail (GraphEngineer, #4341): plain "is on" is too short to read back.
export type Choice = {
  label: string;
  /** What is saved: how the card's place reads (`link_words`' forward words). */
  forward: string;
  /** With both places first, for a choice that reads both ways ("share a border"); null: it reads one way only. */
  pair: string | null;
};

export const NEXT_CHOICES: readonly Choice[] = [
  { label: "Next to", forward: "is next to", pair: "are next to each other" },
  { label: "Sharing a border with", forward: "borders", pair: "share a border" },
  { label: "On the shore (lake)", forward: "is on the shore of", pair: null },
  { label: "On the coast (sea)", forward: "is on the coast of", pair: null },
  { label: "At the foot of", forward: "is at the foot of", pair: null },
];
export const TRAVEL_CHOICES: readonly Choice[] = [
  { label: "By road", forward: "is connected by road to", pair: "are connected by road" },
  { label: "By river", forward: "is connected by river to", pair: "are connected by river" },
  { label: "By sea", forward: "is connected by sea to", pair: "are connected by sea" },
  { label: "By air", forward: "is connected by air to", pair: "are connected by air" },
  { label: "Through a portal", forward: "is linked by a portal to", pair: "are linked by a portal" },
  { label: "Across the wild", forward: "is connected across the wild to", pair: "are connected across the wild" },
  { label: "Not sure", forward: "is connected to", pair: "are connected" },
];

const hintOf = (a: string, b: string, c: Choice): string => (c.pair !== null ? `${a} and ${b} ${c.pair}` : `${a} ${c.forward} ${b}`);

export const placeRoutesWords = {
  nextButton: "↔️ Next to…",
  travelButton: "🛣️ Travel to…",
  /** The search question: "Targos is next to…" or "Travel from Targos to…" (the card's place first). */
  asked: (name: string, travel: boolean): string => (travel ? `Travel from ${name} to…` : `${name} is next to…`),
  searchLabel: "Find a place",
  searching: "Searching…",
  none: "No place found. Type another name.",
  failedSearch: "Couldn't search just now. Try again.",
  /** The sentence the choice finishes: the card's place first. */
  ask: (a: string, b: string, travel: boolean): string => (travel ? `How do you get from ${a} to ${b}? Pick one.` : `${a} is … ${b}. Pick the words that fit.`),
  /** A choice's hint: the card's own sentence for it. */
  hint: hintOf,
  save: "Save",
  back: "↩ Back",
  /** What was saved, in the card's own sentence. */
  saved: (a: string, b: string, c: Choice): string => `Saved: ${hintOf(a, b, c)}.`,
  failed: "Couldn't save that. Try again.",
  refused: "DMbot couldn't save that link. Close this and pick the place again.",
};
