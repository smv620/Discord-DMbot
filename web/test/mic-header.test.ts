// The microphone is allowed on the recap pages only (#2114, "Speak instead"). Every other page keeps
// `microphone=()`. Cloudflare adds the headers of every matching rule together, so the recap rule
// must detach the sitewide Permissions-Policy before it sets its own.
import { readdirSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/** The rules of `public/_headers`: a path line, then its indented header lines. */
function rules(text: string): Map<string, string[]> {
  const found = new Map<string, string[]>();
  let path = "";
  for (const raw of text.split("\n")) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    if (!raw.startsWith(" ")) {
      path = raw.trim();
      found.set(path, []);
    } else found.get(path)?.push(raw.trim());
  }
  return found;
}

describe("the microphone", () => {
  const all = rules(readFileSync("public/_headers", "utf8"));

  it("is off sitewide", () => {
    expect(all.get("/*")).toContain("Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()");
  });

  /** The pages that may ask for the microphone, with the reason: the component that records on them. */
  const MIC_PAGES = ["/account/recaps*", "/account/names*", "/account/questions*"];

  it.each(MIC_PAGES)("is on for %s, which detaches the sitewide value first", (path) => {
    const lines = all.get(path) ?? [];
    const detach = lines.indexOf("! Permissions-Policy");
    const set = lines.findIndex((l) => l.startsWith("Permissions-Policy:"));
    expect(detach).toBeGreaterThanOrEqual(0);
    expect(set).toBeGreaterThan(detach);
    expect(lines[set]).toContain("microphone=(self)");
    // Nothing else is loosened with it.
    expect(lines[set]).toContain("camera=()");
    expect(lines[set]).toContain("geolocation=()");
    expect(lines[set]).toContain("payment=()");
  });

  it("is on for no other path", () => {
    for (const [path, lines] of all) {
      if (path === "/*" || MIC_PAGES.includes(path)) continue;
      expect(lines.filter((l) => /microphone/.test(l)), path).toEqual([]);
    }
  });

  // The Names page was missing here once (#3811): its 🎤 Say it was refused by our own header and the browser never asked.
  // A file that records audio must be known to this test, with the pages that show it in MIC_PAGES above.
  it("knows every file that records audio, so a new page that does cannot be forgotten", () => {
    const recorders = readdirSync("src", { recursive: true })
      .map(String)
      .filter((f) => /\.(tsx|ts|astro)$/.test(f))
      .filter((f) => readFileSync(`src/${f}`, "utf8").includes("getUserMedia"))
      .map((f) => f.replace(/\\/g, "/"))
      .sort();
    // RecapVoice: "Speak instead" on /account/recaps. SayIt: "Say it" on /account/names and in the names to check on
    // /account/questions and /account/recaps. A new recorder means adding its page to MIC_PAGES and to _headers.
    expect(recorders).toEqual(["account/RecapVoice.tsx", "account/SayIt.tsx"]);
  });
});
