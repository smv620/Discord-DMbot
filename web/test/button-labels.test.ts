// Every button the site tells people to press is a real DMbot button (#1893). #1886 once said
// "then Yes" where the button is "Yes, stop recording me"; this catches that kind of slip.
// The label list is read from core's Python, so a renamed button fails here until the site follows.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { buttonPhrases, discordControls, hiddenBotLabels, labelProblems, labelsInPython, siteButtons } from "../scripts/labels.mjs";

const botDir = fileURLToPath(new URL("../../core/src/dmbot/", import.meta.url));
const contentDir = fileURLToPath(new URL("../src/content/", import.meta.url));

// The content files that speak about the bot's buttons. The rest (account, admin, pricing, …) name
// the website's own buttons, which are not the bot's to hold.
const botFacing = ["help.ts", "whats-new.ts", "players.ts", "features.ts", "before-you-start.ts"];

const pythonFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? pythonFiles(join(dir, e.name)) : e.name.endsWith(".py") ? [join(dir, e.name)] : [],
  );

const labels = new Set(pythonFiles(botDir).flatMap((f) => labelsInPython(readFileSync(f, "utf8"))));
const allowed = [...hiddenBotLabels.map((h) => h.label), ...siteButtons, ...discordControls];

describe("button labels on the site", () => {
  it("reads the bot's labels from core", () => {
    expect(labels.size).toBeGreaterThan(200);
    expect(labels).toContain("Yes, stop recording me");
    expect(labels).toContain("Stop recording me");
  });

  it("every label in the hidden list is still in the file it names", () => {
    for (const { label, file } of hiddenBotLabels) {
      // As a whole quoted string (an emoji may come first), so a short word elsewhere can't satisfy it.
      const quoted = new RegExp(`"(?:[^\\p{L}\\p{N}"]*\\s)?${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`, "u");
      expect(readFileSync(join(botDir, file), "utf8"), `${label} in ${file}`).toMatch(quoted);
    }
  });

  for (const file of botFacing) {
    it(`${file} presses only real buttons`, () => {
      const items = buttonPhrases(readFileSync(join(contentDir, file), "utf8"));
      const problems = labelProblems(items, labels, allowed).map(
        (p) => `${file}: "${p.phrase}" is not a DMbot button${p.closest ? `; the closest real one is "${p.closest}"` : ""}`,
      );
      expect(problems).toEqual([]);
    });
  }

  it("names the page, the phrase and the closest real label for the old 'then Yes' wording", () => {
    const old = "press ⚙️ Menu in the private message, then Stop recording me, then Yes.";
    const problems = labelProblems(buttonPhrases(old), labels, allowed);
    expect(problems.map((p) => p.phrase)).toEqual(["Yes"]);
    expect(problems[0]?.closest).toBe("Yes, stop recording me");
    // A real label with more words after it is not that label.
    expect(labelProblems(buttonPhrases("press Menu button."), labels, allowed).map((p) => p.phrase)).toEqual(["Menu button"]);
    // The right wording passes.
    const fixed = "press ⚙️ Menu in the private message, then Stop recording me, then Yes, stop recording me.";
    expect(labelProblems(buttonPhrases(fixed), labels, allowed)).toEqual([]);
  });

  it("counts the phrases it checked", () => {
    const total = botFacing.reduce((n, f) => n + buttonPhrases(readFileSync(join(contentDir, f), "utf8")).length, 0);
    expect(total).toBeGreaterThan(80); // a drop means the reader stopped seeing the site's steps
  });
});
