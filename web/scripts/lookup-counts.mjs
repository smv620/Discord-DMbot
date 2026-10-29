// What DMbot can look up, counted from the rules data at build time (#1932). The Credits page shows
// these numbers, so they are read from the files and never typed by hand. Reading is the only part
// that touches the disk; the counting is plain so the vitest can feed it small examples.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** The kind each entry has in the data, in words a game master would use, in the order shown. */
export const kindNames = {
  spell: "Spells",
  condition: "Conditions",
  monster: "Creatures",
  rule: "Rules",
  feature: "Class features",
  item: "Magic items",
  feat: "Feats",
  species: "Species",
  background: "Backgrounds",
  weapon: "Weapons",
  armor: "Armor",
  tool: "Tools",
  gear: "Gear, mounts and vehicles",
  poison: "Poisons",
  trap: "Traps",
  curse: "Curses",
  disease: "Diseases",
};

/** Every data file of one SRD folder, parsed. */
export function readFolder(dir) {
  return readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => JSON.parse(readFileSync(join(dir, name), "utf8")));
}

/** Entries of each kind in these files: a file names its kind, and an entry may name its own. */
export function countByKind(files) {
  const counts = new Map();
  for (const file of files) {
    for (const entry of file.entries) {
      const kind = entry.kind ?? file.kind;
      counts.set(kind, (counts.get(kind) ?? 0) + 1);
    }
  }
  return counts;
}

/** Kinds in the data that have no plain name yet, so a new one can't show up as "item" on the page. */
export function unnamedKinds(files) {
  return [...countByKind(files).keys()].filter((kind) => !(kind in kindNames));
}

/** The list the page shows: a plain name and a count, only for kinds this SRD has. */
export function lookupList(files) {
  const missing = unnamedKinds(files);
  if (missing.length > 0) throw new Error(`no plain name for the kind: ${missing.join(", ")}`);
  const counts = countByKind(files);
  return Object.entries(kindNames)
    .filter(([kind]) => counts.has(kind))
    .map(([kind, label]) => ({ label, count: counts.get(kind) }));
}
