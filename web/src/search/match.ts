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

const same = (a: string, b: string): boolean => a === b || (a.length >= 4 && b.length >= 4 && (a.startsWith(b) || b.startsWith(a)));

function score(query: string, queryTokens: string[], entry: SearchEntry): number {
  const listed = entry.words.flatMap(tokens);
  const titled = tokens(entry.title);
  const described = tokens(entry.about);
  // A whole listed phrase, as typed, counts most; then each typed word counts once, for the best place it is found, so a page
  // that lists the same word in five phrases gains nothing over a page that lists it once.
  let total = 0;
  for (const phrase of entry.words) if (phrase.includes(" ") && query.includes(phrase)) total += 4;
  for (const q of queryTokens) {
    if (titled.some((w) => same(q, w))) total += 3;
    else if (listed.some((w) => same(q, w))) total += 2;
    else if (described.some((w) => same(q, w))) total += 1;
  }
  return total;
}

/** A page must score this much to be offered: one listed word, or a title word, is enough; a lone word in the page's
 * description is not. */
export const MIN_SCORE = 2;

/** The best page for what was typed, or null when nothing fits. The earlier entry wins a tie. */
export function bestMatch(text: string, index: readonly SearchEntry[] = searchIndex): SearchEntry | null {
  const query = text.toLowerCase().replace(/\s+/g, " ").trim();
  const queryTokens = tokens(query);
  if (queryTokens.length === 0) return null;
  let best: SearchEntry | null = null;
  let top = 0;
  for (const entry of index) {
    const got = score(query, queryTokens, entry);
    if (got > top) {
      best = entry;
      top = got;
    }
  }
  return top >= MIN_SCORE ? best : null;
}
