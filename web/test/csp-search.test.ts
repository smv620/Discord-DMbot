// The script check while the search bar is on (#3855): a page that is not on the list of pages with scripts may carry the
// search bar's island and Astro's start-up scripts, and nothing else.
import { describe, expect, it } from "vitest";

import { strayScriptProblems } from "../scripts/csp.mjs";

const start = "<script>(()=>{var l=(n,t)=>{requestIdleCallback(n)};(self.Astro||(self.Astro={})).idle=l;})();</script>";
const define = '<script>(()=>{customElements.define("astro-island",class{});})();</script>';
const island = (chunk: string) => `<astro-island uid="x" component-url="/_astro/${chunk}" component-export="default" ssr client="idle"></astro-island>`;

describe("scripts on a page without them of its own", () => {
  it("lets the search bar's island and Astro's two start-up scripts through", () => {
    expect(strayScriptProblems(`<body>${start}${define}${island("SearchBar.DPLMmHs_.js")}</body>`)).toEqual([]);
  });
  it("lets a page with no script at all through", () => {
    expect(strayScriptProblems("<body><p>Plain page</p></body>")).toEqual([]);
  });
  it("refuses another island next to the search bar", () => {
    const problems = strayScriptProblems(`${start}${define}${island("SearchBar.DPLMmHs_.js")}${island("Greeting.BFFD_pu0.js")}`);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("Greeting.BFFD_pu0.js");
  });
  it("refuses a plain script with a src", () => {
    expect(strayScriptProblems(`${start}${define}${island("SearchBar.DPLMmHs_.js")}<script src="/x.js"></script>`)).toEqual(["a script with a src"]);
  });
  it("refuses an inline script that is not Astro's", () => {
    expect(strayScriptProblems(`${define}<script>alert(1)</script>`)).toEqual(["an inline script that is not Astro's start-up"]);
  });
  it("leaves JSON data scripts alone", () => {
    expect(strayScriptProblems('<script type="application/ld+json">{"a":1}</script>')).toEqual([]);
  });
});
