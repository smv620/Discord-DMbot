// The microphone is allowed on the recap pages only (#2114, "Speak instead"). Every other page keeps
// `microphone=()`. Cloudflare adds the headers of every matching rule together, so the recap rule
// must detach the sitewide Permissions-Policy before it sets its own.
import { readFileSync } from "node:fs";

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

  it("is on for the recap pages, which detach the sitewide value first", () => {
    const lines = all.get("/account/recaps*") ?? [];
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
      if (path === "/*" || path === "/account/recaps*") continue;
      expect(lines.filter((l) => /microphone/.test(l)), path).toEqual([]);
    }
  });
});
