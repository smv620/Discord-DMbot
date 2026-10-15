// After `npm run build`: the page changes must stay inside their budget, stay off for people who
// asked for less motion, and stay off the legal pages (#1311). Usage: node scripts/check-transitions.mjs [dist-dir]
import { transitionProblems } from "./transitions.mjs";

const dist = process.argv[2] ?? new URL("../dist/", import.meta.url).pathname;
const problems = transitionProblems(dist);
if (problems.length > 0) {
  for (const problem of problems) console.error(`::error title=page changes::${problem}`);
  process.exit(1);
}
console.log("page changes: within budget, off for less motion, off on the legal pages.");
