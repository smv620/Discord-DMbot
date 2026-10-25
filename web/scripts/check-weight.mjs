// After `npm run build`: how much each page weighs, against a budget (#1947). Usage:
// node scripts/check-weight.mjs [dist-dir]
//
// Budgets are in KB (1024 bytes), counted before compression: the page's own HTML, and everything
// it loads from this site (CSS, JS, fonts, images; each file once). The site is static and should
// stay quick on a phone at the table, so a large picture or a new script should fail here rather
// than slip in. They started at 60 KB of HTML and 350 KB per page, then were tightened to today's
// largest plus about 20%, so a page that grows by a fifth fails and someone looks at why. The link
// preview picture (share.png, named in a page's meta tags) is not loaded by the page, but it is
// held to the same picture limit.
import { fileURLToPath } from "node:url";

import { checkBuild, table } from "./weight.mjs";

// Measured on 2026-10-18 (#1947), following the scripts a page loads: the largest HTML is help.html at
// 39.5 KB, and the largest page in all is /account at 142.6 KB (its script islands and what they
// import). Each plus 20%, rounded up to the next 5 KB.
// Raised on 2026-10-24 (#2617): turning narration on brings the audio choice, the player and the names
// panel into /account/recaps (176.7 KB) and Help's narration steps into help.html (50.3 KB). Each is
// legitimate and was looked at; both budgets are today's largest plus about 5% of headroom.
// Raised on 2026-10-25 (#2520) to 190 KB for the recaps page (187.8 KB), then lowered again the same day
// (#2690): the recap page's DM-only parts (the recaps maker, fix-a-word, the names list, the player and
// the switched panels) load on demand with import(), so the recaps page is 174 KB and the largest page
// is /account at 178.8 KB. The budget is back at 185 KB. A chunk loaded on demand is not part of a page's
// weight, so it has its own limit.
export const budgets = {
  htmlKB: 52,
  totalKB: 185,
  onDemandKB: 45, // the most one chunk loaded on demand may weigh (#2690)
  imageKB: 150, // no picture bigger than this: resize or recompress it, don't drop it
};

const dist = process.argv[2] ?? fileURLToPath(new URL("../dist/", import.meta.url));
const { rows, fonts, problems } = checkBuild(dist, budgets);

console.log(table(rows, budgets));
console.log(`\nFonts loaded: ${fonts.length === 0 ? "none (the pages use the reader's own fonts)" : fonts.join(", ")}`);
if (problems.length > 0) {
  console.error(`\nPage weight problems:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log(`\nEvery page is within its budget (HTML ${budgets.htmlKB} KB, everything ${budgets.totalKB} KB); no picture over ${budgets.imageKB} KB.`);
