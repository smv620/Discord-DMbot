// Keeps docs/GO_LIVE.md and the site's *_LIVE switches in step (#1881): a switch added without its
// row, or removed and left in the doc, would send the owner to the wrong check on go-live day.
// Pure functions, so test/golive.test.ts can feed them made-up text; check-golive.mjs reads the real files.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SWITCH = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_LIVE\b/g;
const EXPORT = /export const ([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_LIVE)\b/g;
// A table row starts with the switch in backticks; its last cell says what it goes live with.
const ROW = /^\|\s*`([A-Z][A-Z0-9_]*_LIVE)`/;

function tsFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? tsFiles(full) : full.endsWith(".ts") ? [full] : [];
  });
}

/** Every `export const NAME_LIVE` under web/src/content, as a sorted list of names. */
export function switchesInCode(contentDir) {
  const names = new Set();
  for (const file of tsFiles(contentDir)) {
    for (const [, name] of readFileSync(file, "utf8").matchAll(EXPORT)) names.add(name);
  }
  return [...names].sort();
}

/** Every NAME_LIVE the doc names anywhere (table rows and prose), sorted. */
export function switchesInDoc(doc) {
  return [...new Set(doc.match(SWITCH) ?? [])].sort();
}

/** The table rows: switch name and the text of the last cell ("goes live with"). */
export function docRows(doc) {
  const rows = [];
  for (const line of doc.split("\n")) {
    const match = ROW.exec(line);
    if (!match) continue;
    const cells = line.split("|").map((cell) => cell.trim());
    // A row starts and ends with "|", so the first and last entries are empty.
    rows.push({ name: match[1], deploy: cells.length >= 3 ? (cells[cells.length - 2] ?? "") : "" });
  }
  return rows;
}

/** What is wrong, as plain sentences naming the switch and the side it is missing from. */
export function goLiveProblems(codeSwitches, doc) {
  const problems = [];
  const inDoc = new Set(switchesInDoc(doc));
  const inCode = new Set(codeSwitches);
  for (const name of codeSwitches) {
    if (!inDoc.has(name)) problems.push(`${name} is in web/src/content but not in docs/GO_LIVE.md`);
  }
  for (const name of inDoc) {
    if (!inCode.has(name)) problems.push(`${name} is in docs/GO_LIVE.md but not in web/src/content`);
  }
  for (const { name, deploy } of docRows(doc)) {
    if (deploy === "") problems.push(`${name} has no "goes live with" in docs/GO_LIVE.md`);
  }
  return problems;
}
