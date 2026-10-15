// Guards for the page changes (#1289, #1311), read from a finished build in dist/. Shared by
// check-transitions.mjs (the command CI runs after `npm run build`) and its test. The rules come
// from docs/PLAN.md, "Page changes: magic dust, and runes on the banner".
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

/** The PLAN's limit on what the effect adds to the site ("at most about 15 KB"). */
export const BUDGET_BYTES = 15 * 1024;

// The only two forms of the opt-in rule the pages may carry (Base.astro). Everything else that
// says @view-transition is an unconditional opt-in, which would ignore "less motion".
const OPT_IN = "@media (prefers-reduced-motion:no-preference){@view-transition{navigation:auto}}";
const OPT_OUT = "@view-transition{navigation:none}";

function filesWith(dir, extension) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return filesWith(full, extension);
    return full.endsWith(extension) ? [full] : [];
  });
}

/** The text of every top-level rule in some CSS (an at-rule counts as one, with its block). */
export function topLevelRules(css) {
  const rules = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}") {
      depth--;
      if (depth === 0) {
        rules.push(css.slice(start, i + 1));
        start = i + 1;
      }
    }
  }
  return rules;
}

/**
 * The bytes the effect adds to the built CSS: every top-level rule that is about the page
 * change (its selectors, its @property, its less-motion block). The sparkle mask and the rune
 * picture are inline data in those rules, so they are counted too.
 */
export function transitionBytes(css) {
  return topLevelRules(css)
    .filter((rule) => /view-transition|@property\s+--dust/.test(rule))
    .reduce((sum, rule) => sum + Buffer.byteLength(rule), 0);
}

/** Problems with the size of the effect in a build's CSS files. */
export function sizeProblems(dist) {
  const bytes = filesWith(dist, ".css").reduce(
    (sum, file) => sum + transitionBytes(readFileSync(file, "utf8")),
    0,
  );
  const note = `the page-change CSS is ${bytes} bytes; the budget is ${BUDGET_BYTES} (PLAN, "Page changes")`;
  if (bytes === 0) return [`${note}: none found, so the effect (or this check) is broken`];
  return bytes > BUDGET_BYTES ? [note] : [];
}

/** Problems with a page opting in when the visitor asked for less motion. */
export function reducedMotionProblems(dist) {
  const problems = [];
  for (const file of [...filesWith(dist, ".html"), ...filesWith(dist, ".css")]) {
    const rest = readFileSync(file, "utf8").replaceAll(OPT_IN, "").replaceAll(OPT_OUT, "");
    if (rest.includes("@view-transition")) {
      problems.push(
        `${relative(dist, file)}: @view-transition outside "prefers-reduced-motion:no-preference", ` +
          "so people who asked for less motion would get the effect",
      );
    }
  }
  return problems;
}

/** Problems with a legal page that isn't plain (no dust, no runes, in or out). */
export function legalProblems(dist) {
  const problems = [];
  const pages = filesWith(join(dist, "legal"), ".html");
  if (pages.length === 0) problems.push("dist/legal has no pages, so the plain-legal-pages rule can't be checked");
  for (const file of pages) {
    const html = readFileSync(file, "utf8");
    const name = relative(dist, file);
    if (!html.includes(OPT_OUT)) problems.push(`${name} does not opt out of page changes (it must be plain)`);
    if (!html.includes("h1{view-transition-name:none}")) problems.push(`${name} still names its banner for the rune effect`);
    if (html.includes("navigation:auto")) problems.push(`${name} opts in to page changes`);
  }
  return problems;
}

/** Every problem found in a build. */
export function transitionProblems(dist) {
  return [...sizeProblems(dist), ...reducedMotionProblems(dist), ...legalProblems(dist)];
}
