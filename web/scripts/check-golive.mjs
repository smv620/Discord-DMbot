// Every go-live switch in the site is in docs/GO_LIVE.md, and every one there is in the site (#1881).
// Usage: node scripts/check-golive.mjs
import { readFileSync } from "node:fs";
import { goLiveProblems, switchesInCode } from "./golive.mjs";

const content = new URL("../src/content/", import.meta.url).pathname;
const doc = readFileSync(new URL("../../docs/GO_LIVE.md", import.meta.url), "utf8");
const problems = goLiveProblems(switchesInCode(content), doc);
if (problems.length > 0) {
  for (const problem of problems) console.error(`::error title=go-live map::${problem}`);
  process.exit(1);
}
console.log(`go-live map: ${switchesInCode(content).length} switches, all in docs/GO_LIVE.md.`);
