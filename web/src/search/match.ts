// The match behind the website's search bar (#3855): which page best fits what someone typed. It runs in the browser on every
// keystroke, so it is plain code with no outside service and nothing is kept. A word counts when it is the same as, or a
// longer form of, one a page lists (so "episodes" finds "episode"); a whole listed phrase in the query counts for more.
// The page lists, not the spelling, decide: add words to web/src/content/search-index.ts, not tricks here.
import { type SearchEntry, searchIndex } from "../content/search-index";

const FILLER = new Set(["a", "an", "the", "of", "to", "for", "in", "on", "at", "is", "it", "my", "me", "i", "do", "does", "and", "or", "with", "about", "how", "what", "where", "can", "you", "your", "get", "show", "find", "need", "want", "please"]);

// Only a plural "s" is cut ("episodes" finds "episode"); cutting more turns "names" into "nam".
const stem = (word: string): string => word.replace(/(?<!s)s$/, "");

/** The words of a text, lower-case, with the little ones dropped and each cut back to its root. */
export function tokens(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9&' ]+/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 0 && !FILLER.has(w))
    .map((w) => (w.length > 4 ? stem(w) : w));
}

/** The same word, or a longer form of it ("episodes" finds "episode"). */
const exact = (a: string, b: string): boolean => a === b || (a.length >= 4 && b.length >= 4 && (a.startsWith(b) || b.startsWith(a)));

/** Edit distance with a swap of two neighbouring letters counting as one change (so "recpas" is one away from "recap"),
 * given up on as soon as it must pass `limit`. Returns limit + 1 then. */
export function distance(a: string, b: string, limit: number): number {
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let before: number[] = [];
  let last = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let best = Math.min(last[j]! + 1, row[j - 1]! + 1, last[j - 1]! + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) best = Math.min(best, before[j - 2]! + 1);
      row.push(best);
    }
    if (Math.min(...row) > limit) return limit + 1;
    before = last;
    last = row;
  }
  return last[b.length]!;
}

/** A typo of the word: both at least 5 letters and one change apart, or both at least 8 and at most two apart. Short words
 * are never matched this way ("cots" is not "cost"): too many real words sit one change from each other. */
const near = (a: string, b: string): boolean => {
  const long = Math.min(a.length, b.length);
  if (long < 5) return false;
  return distance(a, b, long >= 8 ? 2 : 1) <= (long >= 8 ? 2 : 1);
};

// What a typed word is worth where it is found: a page's title, a word it lists, its plain line. A typo is worth one less
// than the right spelling (and nothing in the plain line), so a correctly spelled page still wins a tie.
const WORTH = { title: 3, listed: 2, described: 1 } as const;

function worth(q: string, places: { title: string[]; listed: string[]; described: string[] }): { got: number; strong: boolean } {
  let best = 0;
  let strong = false;
  for (const place of ["title", "listed", "described"] as const) {
    const base = WORTH[place];
    for (const w of places[place]) {
      if (exact(q, w)) best = Math.max(best, base);
      else if (base > 1 && base - 1 > best && near(q, w)) best = base - 1;
      // A word of the title or of the page's list counts as a real hit, a typo of one included; a lone word of the plain
      // line does not (see MIN_SCORE).
      if (place !== "described" && best > 0 && (exact(q, w) || near(q, w))) strong = true;
    }
  }
  return { got: best, strong };
}

function score(query: string, queryTokens: string[], entry: SearchEntry): { total: number; strong: boolean } {
  const places = { listed: entry.words.flatMap(tokens), title: tokens(entry.title), described: tokens(entry.about) };
  // A whole listed phrase, as typed, counts most; then each typed word counts once, for the best place it is found, so a page
  // that lists the same word in five phrases gains nothing over a page that lists it once.
  let total = 0;
  let strong = false;
  for (const phrase of entry.words) if (phrase.includes(" ") && query.includes(phrase)) {
    total += 4;
    strong = true;
  }
  for (const q of queryTokens) {
    const one = worth(q, places);
    total += one.got;
    strong ||= one.strong;
  }
  return { total, strong };
}

/** A page is offered when a typed word is in its title or its list (a typo of one counts), or when its plain line gives at
 * least this much: one lone word of the plain line is not enough. */
export const MIN_SCORE = 2;

/** The best page for what was typed, or null when nothing fits. The earlier entry wins a tie. */
export function bestMatch(text: string, index: readonly SearchEntry[] = searchIndex): SearchEntry | null {
  const query = text.toLowerCase().replace(/\s+/g, " ").trim();
  const queryTokens = tokens(query);
  if (queryTokens.length === 0) return null;
  let best: SearchEntry | null = null;
  let top = 0;
  for (const entry of index) {
    const { total, strong } = score(query, queryTokens, entry);
    if ((strong || total >= MIN_SCORE) && total > top) {
      best = entry;
      top = total;
    }
  }
  return best;
}
