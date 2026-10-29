// Keeps docs/GO_LIVE.md and the site's *_LIVE switches in step (#1881): a switch added without its
// row, or removed and left in the doc, would send the owner to the wrong check on go-live day.
// Pure functions, so test/golive.test.ts can feed them made-up text; check-golive.mjs reads the real files.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

// The doc names a switch only in backticks, so a word like GO_LIVE.md in prose is not read as one.
const SWITCH = /`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_LIVE)`/g;
const EXPORT = /export\s+(?:const|let)\s+([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_LIVE)\b/g;
// A "goes live with" cell holding only one of these says nothing about the deploy.
const PLACEHOLDER = /^(?:[-—–]+|tbd|todo|\?+)$/i;
// A table row starts with the switch in backticks; its last cell says what it goes live with.
const ROW = /^\|\s*`([A-Z][A-Z0-9_]*_LIVE)`/;

function tsFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? tsFiles(full) : /\.(?:ts|tsx|mts)$/.test(full) ? [full] : [];
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

/** Every NAME_LIVE the doc names in backticks, in table rows or prose, sorted. A switch named only in
 * prose counts on purpose: a switch that is already true (HOMEBREW_LINKS_LIVE) has no table row. */
export function switchesInDoc(doc) {
  return [...new Set([...doc.matchAll(SWITCH)].map((match) => match[1]))].sort();
}

/** The table rows: switch name and the text of the last cell ("goes live with"). */
export function docRows(doc) {
  const rows = [];
  for (const line of doc.split("\n")) {
    const match = ROW.exec(line);
    if (!match) continue;
    // Split on "|" but not on an escaped "\|" inside a cell.
    const cells = line.split(/(?<!\\)\|/).map((cell) => cell.trim());
    // A row starts and ends with "|", so the first and last entries are empty. A row with no closing
    // "|" has its last cell in that slot instead, which must not pass as filled in.
    const closed = cells[cells.length - 1] === "";
    rows.push({ name: match[1], deploy: closed && cells.length >= 3 ? (cells[cells.length - 2] ?? "") : "" });
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
    if (deploy === "" || PLACEHOLDER.test(deploy)) problems.push(`${name} has no "goes live with" in docs/GO_LIVE.md`);
  }
  return problems;
}
