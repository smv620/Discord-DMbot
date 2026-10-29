// The Credits page (#1923) says what the SRD's licence asks it to say, and says it the way the data's
// own ATTRIBUTION.md files do. The statements are read from those files here, so the page can't drift.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { creditsNotAffiliated, sources, whatDmbotDid } from "../src/content/credits";
import { publicPaths } from "../src/content/site";

const root = fileURLToPath(new URL("../../", import.meta.url));

/** The statement is the first paragraph under the heading of an ATTRIBUTION.md. */
export const statementIn = (text: string): string => text.split("\n\n")[1]?.trim() ?? "";

const file = (path: string): string => readFileSync(`${root}${path}`, "utf8");

describe("Credits page", () => {
  it("has the two statements word for word from the ATTRIBUTION.md files", () => {
    expect(sources).toHaveLength(2);
    for (const s of sources) {
      expect(statementIn(file(s.path)), s.path).toBe(s.statement);
      expect(s.statement).toContain("by Wizards of the Coast LLC");
      expect(s.statement).toContain("https://creativecommons.org/licenses/by/4.0/legalcode");
    }
    expect(sources[0]?.statement).toContain("https://www.dndbeyond.com/srd");
    expect(sources[1]?.statement).toContain("https://dnd.wizards.com/resources/systems-reference-document");
  });

  it("fails when one word of a statement is changed", () => {
    const real = file(sources[0]!.path);
    const changed = real.replace("includes material", "includes some material");
    expect(statementIn(changed)).not.toBe(sources[0]!.statement);
  });

  it("links each ATTRIBUTION.md at its path on main, and the file is there", () => {
    for (const s of sources) {
      expect(s.file).toBe(`https://github.com/smv620/Discord-DMbot/blob/main/${s.path}`);
      expect(file(s.path).length).toBeGreaterThan(0);
    }
  });

  it("says what DMbot did, and that Wizards of the Coast does not endorse it", () => {
    expect(whatDmbotDid).toContain("took the text out of the PDFs");
    expect(creditsNotAffiliated).toMatch(/not affiliated with or endorsed by Wizards of the Coast/);
  });

  it("is a public page", () => {
    expect(publicPaths).toContain("/credits");
  });
});
