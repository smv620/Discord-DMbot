// The go-live map check (#1881): docs/GO_LIVE.md and the site's *_LIVE switches must name each other.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { docRows, goLiveProblems, switchesInCode, switchesInDoc } from "../scripts/golive.mjs";

const doc = [
  "| Switch | Shows | Goes live with |",
  "|---|---|---|",
  "| `ALPHA_LIVE` (help.ts) | A | **next deploy** |",
  "| `BETA_LIVE` (help.ts) | B | **owner check** |",
  "",
  "`GAMMA_LIVE` is already `true` and is not in the table.",
].join("\n");

describe("go-live map check", () => {
  it("passes when the code and the doc name the same switches", () => {
    expect(goLiveProblems(["ALPHA_LIVE", "BETA_LIVE", "GAMMA_LIVE"], doc)).toEqual([]);
  });

  it("names a switch that is in the code but not in the doc", () => {
    expect(goLiveProblems(["ALPHA_LIVE", "BETA_LIVE", "GAMMA_LIVE", "DELTA_LIVE"], doc)).toEqual([
      "DELTA_LIVE is in web/src/content but not in docs/GO_LIVE.md",
    ]);
  });

  it("names a switch that is in the doc but not in the code", () => {
    expect(goLiveProblems(["ALPHA_LIVE", "GAMMA_LIVE"], doc)).toEqual([
      "BETA_LIVE is in docs/GO_LIVE.md but not in web/src/content",
    ]);
  });

  it("names a row with nothing written for the deploy", () => {
    const empty = doc.replace("**owner check**", "");
    expect(goLiveProblems(["ALPHA_LIVE", "BETA_LIVE", "GAMMA_LIVE"], empty)).toEqual([
      'BETA_LIVE has no "goes live with" in docs/GO_LIVE.md',
    ]);
  });

  it("rejects a placeholder and a row with no closing bar", () => {
    const placeholder = doc.replace("**owner check**", "TBD");
    expect(goLiveProblems(["ALPHA_LIVE", "BETA_LIVE", "GAMMA_LIVE"], placeholder)).toEqual([
      'BETA_LIVE has no "goes live with" in docs/GO_LIVE.md',
    ]);
    const open = doc.replace("**owner check** |", "**owner check**");
    expect(goLiveProblems(["ALPHA_LIVE", "BETA_LIVE", "GAMMA_LIVE"], open)).toEqual([
      'BETA_LIVE has no "goes live with" in docs/GO_LIVE.md',
    ]);
  });

  it("reads rows and switch names the way the doc writes them", () => {
    expect(switchesInDoc(doc)).toEqual(["ALPHA_LIVE", "BETA_LIVE", "GAMMA_LIVE"]);
    expect(docRows(doc)).toEqual([
      { name: "ALPHA_LIVE", deploy: "**next deploy**" },
      { name: "BETA_LIVE", deploy: "**owner check**" },
    ]);
    // "*_LIVE" in prose is the pattern, not a switch.
    expect(switchesInDoc("each `*_LIVE` switch and GO_LIVE.md")).toEqual([]);
  });

  it("passes on the real files", () => {
    const real = readFileSync(new URL("../../docs/GO_LIVE.md", import.meta.url), "utf8");
    const codeSwitches = switchesInCode(new URL("../src/content/", import.meta.url).pathname);
    expect(codeSwitches.length).toBeGreaterThan(10);
    expect(goLiveProblems(codeSwitches, real)).toEqual([]);
  });
});
