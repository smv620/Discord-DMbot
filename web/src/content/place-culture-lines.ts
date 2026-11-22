// A place's culture and who lives here, as the words Discord uses (#4412): the lines on the Names page's panel, in the player's table
// view and in World notes all come from these, so the three stay level. Each function takes the rows of one layer.
import { placeWorldWords as w } from "./place-world-words";

const LINEAGES = ["dwarf", "elf", "dragonborn", "gnome", "goliath", "halfling", "human", "orc", "tiefling"] as const;
export { LINEAGES };

/** The part of a culture or who-lives-here row the words need: its lineage or word, its named culture, its strength or share. */
export interface CultureLineRow {
  value: string;
  words: string;
  how: string;
  culture?: string | null;
  read?: boolean;
}

/** The name a culture row reads as: a named culture's name, the DM's own words, or the lineage's adjective. A named culture with no
 * name the page can show reads as "a culture of your own". */
export function cultureName(r: CultureLineRow): string {
  if (r.culture) return r.culture;
  if (r.value === "other") return r.words;
  return w.culture.adjective[r.value] ?? (r.value === "" ? w.culture.ownCulture : r.value);
}

/** The name a who-lives-here row reads as: a named culture's name, the DM's own words, or the lineage's plural. */
export function peopleName(r: CultureLineRow): string {
  if (r.culture) return r.culture;
  if (r.value === "other") return r.words;
  return w.culture.plural[r.value] ?? (r.value === "" ? w.culture.ownCulture : r.value);
}

/** The culture's words for these rows: each with its strength in brackets, joined by commas. Empty when there are none. */
export function cultureWords(rows: CultureLineRow[]): string {
  return rows.map((r) => `${cultureName(r)}${r.how ? ` (${w.culture.strengths[r.how] ?? r.how})` : ""}${r.read ? w.readMark : ""}`).join(", ");
}

/** The who-lives-here words for these rows: each with its share in front, joined by commas. Empty when there are none. */
export function populationWords(rows: CultureLineRow[]): string {
  return rows.map((r) => `${r.how ? `${w.population.shares[r.how] ?? r.how} ` : ""}${peopleName(r)}${r.read ? w.readMark : ""}`).join(", ");
}

/** The culture line on a panel or in the table view, or null when there are no rows. */
export function cultureLine(rows: CultureLineRow[]): string | null {
  return rows.length === 0 ? null : w.cultureLine(cultureWords(rows));
}

/** The who-lives-here line, or null when there are no rows. */
export function populationLine(rows: CultureLineRow[]): string | null {
  return rows.length === 0 ? null : w.populationLine(populationWords(rows));
}
