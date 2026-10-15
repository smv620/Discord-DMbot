// Guards for the page changes (#1289, #1311), read from a finished build in dist/. Shared by
// check-transitions.mjs (the command CI runs after `npm run build`) and its test. The rules come
// from docs/PLAN.md, "Page changes: magic dust, and runes on the banner".
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

/** The PLAN's limit on what the effect adds to the site ("at most about 15 KB"). */
export const BUDGET_BYTES = 15 * 1024;

// The only two forms of the opt-in rule the pages may carry (Base.astro). Everything else that
// says @view-transition is an unconditional opt-in, which would ignore "less motion".
// Compared as exact text, so a reformatted Base.astro fails every page (safe, and the message
// says why); a test keeps these equal to what Base.astro really writes.
export const OPT_IN = "@media (prefers-reduced-motion:no-preference){@view-transition{navigation:auto}}";
export const OPT_OUT = "@view-transition{navigation:none}";
export const OPT_OUT_BANNER = "h1{view-transition-name:none}";

function filesWith(dir, extension) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return filesWith(full, extension);
    return full.endsWith(extension) ? [full] : [];
  });
}

/**
 * The text of every top-level rule in some CSS (an at-rule counts as one, with its block).
 * Braces inside quotes and url(...) don't count, and a top-level statement such as @import
 * ends where its semicolon is, so it is never glued onto the next rule.
 */
export function topLevelRules(css) {
  const rules = [];
  let depth = 0;
  let start = 0;
  let quote = "";
  let inUrl = false;
  for (let i = 0; i < css.length; i++) {
    const c = css[i];
    if (quote) {
      if (c === "\\") i++;
      else if (c === quote) quote = "";
    } else if (inUrl) {
      if (c === ")") inUrl = false;
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (c === "(" && /url$/i.test(css.slice(Math.max(0, i - 3), i)) && !/^\s*["']/.test(css.slice(i + 1, i + 3))) {
      // An unquoted url(...) ends at its first ")"; a quoted one is handled as a string.
      inUrl = true;
    } else if (c === "{") {
      depth++;
    } else if (c === "}") {
      depth--;
      if (depth === 0) {
        rules.push(css.slice(start, i + 1));
        start = i + 1;
      }
    } else if (c === ";" && depth === 0) {
      start = i + 1;
    }
  }
  return rules;
}

/**
 * The bytes the effect adds to a piece of CSS: every top-level rule that is about the page
 * change (its selectors, its @property, its keyframes, its less-motion block). The sparkle
 * mask and the rune picture are inline data in those rules, so they are counted too. A rule
 * the minifier merged with unrelated ones counts whole, which can only over-count.
 */
export function transitionBytes(css) {
  return topLevelRules(css)
    .filter((rule) => /view-transition|@property\s+--dust|@keyframes\s+(dust|runes|banner)/.test(rule))
    .reduce((sum, rule) => sum + Buffer.byteLength(rule), 0);
}

/** The CSS in a page's <style> elements, minus the small opt-in and opt-out rules every page carries. */
function inlineCss(html) {
  return [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/gi)]
    .map(([, css]) => css.replaceAll(OPT_IN, "").replaceAll(OPT_OUT, "").replaceAll(OPT_OUT_BANNER, ""))
    .join("");
}

/** Problems with the size of the effect in a build's CSS files. */
export function sizeProblems(dist) {
  // Astro inlines a small stylesheet into every page, so look there too. The same sheet is on
  // each page, so only the biggest page's share is added.
  const inlined = filesWith(dist, ".html").map((file) => transitionBytes(inlineCss(readFileSync(file, "utf8"))));
  const bytes =
    filesWith(dist, ".css").reduce((sum, file) => sum + transitionBytes(readFileSync(file, "utf8")), 0) +
    Math.max(0, ...inlined);
  if (bytes === 0) {
    return [
      "No page-change CSS found in dist/. Check that the view-transition rules are still in " +
        "src/styles/transitions.css, and that `npm run build` ran first. (Both the built .css files " +
        "and the <style> in each page are searched.)",
    ];
  }
  return bytes > BUDGET_BYTES
    ? [
        `The page-change CSS is ${bytes} bytes; the budget is ${BUDGET_BYTES} (PLAN, "Page changes"). ` +
          "Shrink the sparkle mask or the rune picture in src/styles/transitions.css, or ask Supervisor to change the budget.",
      ]
    : [];
}

/** Problems with a page opting in when the visitor asked for less motion. */
export function reducedMotionProblems(dist) {
  const problems = [];
  for (const file of [...filesWith(dist, ".html"), ...filesWith(dist, ".css")]) {
    const rest = readFileSync(file, "utf8").replaceAll(OPT_IN, "").replaceAll(OPT_OUT, "");
    if (rest.includes("@view-transition")) {
      problems.push(
        `${relative(dist, file)}: @view-transition outside "prefers-reduced-motion:no-preference", ` +
          "so people who asked for less motion would get the effect. Wrap it exactly as Base.astro does: " +
          "@media (prefers-reduced-motion:no-preference){...} (the check compares the exact text, " +
          "so a reformatted Base.astro fails too).",
      );
    }
  }
  return problems;
}

/** Problems with a legal page that isn't plain (no dust, no runes, in or out). */
export function legalProblems(dist) {
  const problems = [];
  const pages = filesWith(join(dist, "legal"), ".html");
  if (pages.length === 0) problems.push("dist/legal has no pages, so the plain-legal-pages rule can't be checked. Run `npm run build` first.");
  for (const file of pages) {
    const html = readFileSync(file, "utf8");
    const name = relative(dist, file);
    if (!html.includes(OPT_OUT)) problems.push(`${name} does not opt out of page changes. Legal pages must be plain: build them with LegalPage, which passes plain to Base`);
    if (!html.includes(OPT_OUT_BANNER)) problems.push(`${name} still names its banner for the rune effect. Legal pages must be plain: build them with LegalPage`);
    if (html.includes("@view-transition{navigation:auto")) problems.push(`${name} opts in to page changes. Legal pages must be plain: build them with LegalPage`);
  }
  return problems;
}

/** Every problem found in a build. */
export function transitionProblems(dist) {
  return [...sizeProblems(dist), ...reducedMotionProblems(dist), ...legalProblems(dist)];
}
