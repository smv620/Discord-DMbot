// Every button the site tells people to press is a real DMbot button (#1893). #1886 once said
// "then Yes" where the button is "Yes, stop recording me"; this catches that kind of slip.
// The label list is read from core's Python, so a renamed button fails here until the site follows.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { buttonPhrases, discordControls, hiddenBotLabels, labelProblems, pendingBotLabels, labelsInPython, siteButtons, unmark } from "../scripts/labels.mjs";

const botDir = fileURLToPath(new URL("../../core/src/dmbot/", import.meta.url));
const contentDir = fileURLToPath(new URL("../src/content/", import.meta.url));

// The content files that speak about the bot's buttons. The rest (account, admin, pricing, …) name
// the website's own buttons, which are not the bot's to hold.
/** A content file's text; What's new is one file per entry now (#2468), so its folder counts too. */
function contentText(file: string): string {
  const own = readFileSync(join(contentDir, file), "utf8");
  if (file !== "whats-new.ts") return own;
  const folder = join(contentDir, "whats-new");
  return [own, ...readdirSync(folder).map((f) => readFileSync(join(folder, f), "utf8"))].join("\n");
}

const botFacing = ["help.ts", "whats-new.ts", "players.ts", "features.ts", "before-you-start.ts"];

const pythonFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? pythonFiles(join(dir, e.name)) : e.name.endsWith(".py") ? [join(dir, e.name)] : [],
  );

const labels = new Set(pythonFiles(botDir).flatMap((f) => labelsInPython(readFileSync(f, "utf8"))));
const allowed = [...hiddenBotLabels.map((h) => h.label), ...pendingBotLabels.map((p) => p.label), ...siteButtons, ...discordControls];

describe("button labels on the site", () => {
  it("reads the bot's labels from core", () => {
    expect(labels.size).toBeGreaterThan(200);
    expect(labels).toContain("Yes, stop recording me");
    expect(labels).toContain("Stop recording me");
    // An f-string label, worked out from MIN_AGE in the same file.
    expect(labels).toContain("I'm 16 or older, record me");
  });

  it("every label in the hidden list is still in the file it names", () => {
    for (const { label, file } of hiddenBotLabels) {
      // As a whole quoted string (an emoji may come first), so a short word elsewhere can't satisfy it.
      const quoted = new RegExp(`"(?:[^\\p{L}\\p{N}"]*\\s)?${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`, "u");
      expect(readFileSync(join(botDir, file), "utf8"), `${label} in ${file}`).toMatch(quoted);
    }
  });

  it("a label waiting for its code is removed from the pending list once core has it", () => {
    for (const { label, issue } of pendingBotLabels) {
      expect(labels, `${label} is in core now: remove it from pendingBotLabels (#${issue})`).not.toContain(label);
    }
  });

  for (const file of botFacing) {
    it(`${file} presses only real buttons`, () => {
      const items = buttonPhrases(contentText(file));
      const problems = labelProblems(items, labels, allowed).map(
        (p) => `${file}: "${p.phrase}" is not a DMbot button${p.closest ? `; the closest real one is "${p.closest}"` : ""}`,
      );
      expect(problems).toEqual([]);
    });
  }

  // The owner's checks name buttons in bold and in prose; marks are stripped, then the same reader runs.
  it("docs/OWNER_CHECKS.md presses only real buttons", () => {
    const text = unmark(readFileSync(fileURLToPath(new URL("../../docs/OWNER_CHECKS.md", import.meta.url)), "utf8"));
    const problems = labelProblems(buttonPhrases(text), labels, allowed).map(
      (p) => `OWNER_CHECKS.md: "${p.phrase}" is not a DMbot button${p.closest ? `; the closest real one is "${p.closest}"` : ""}`,
    );
    expect(problems).toEqual([]);
    expect(buttonPhrases(text).length).toBeGreaterThan(40);
  });

  it("sees a bold button that does not exist, as #1905's first head had", () => {
    const old = "in the private message DMbot sends you, press **Yes**, then press **Helped**.";
    expect(labelProblems(buttonPhrases(unmark(old)), labels, allowed).map((p) => p.phrase)).toEqual(["Yes", "Helped"]);
    expect(labelsInPython('MIN_AGE = 16  # why\nCONSENT_LABEL = f"I\'m {MIN_AGE} or older"')).toEqual(["I'm 16 or older"]);
  });

  it("names the page, the phrase and the closest real label for the old 'then Yes' wording", () => {
    const old = "press 🗂️ Menu in the private message, then Stop recording me, then Yes.";
    const problems = labelProblems(buttonPhrases(old), labels, allowed);
    expect(problems.map((p) => p.phrase)).toEqual(["Yes"]);
    expect(problems[0]?.closest).toBe("Yes, stop recording me");
    // A real label with more words after it is not that label.
    expect(labelProblems(buttonPhrases("press Menu button."), labels, allowed).map((p) => p.phrase)).toEqual(["Menu button"]);
    // The right wording passes.
    const fixed = "press 🗂️ Menu in the private message, then Stop recording me, then Yes, stop recording me.";
    expect(labelProblems(buttonPhrases(fixed), labels, allowed)).toEqual([]);
  });

  it("counts the phrases it checked", () => {
    const total = botFacing.reduce((n, f) => n + buttonPhrases(contentText(f)).length, 0);
    expect(total).toBeGreaterThan(80); // a drop means the reader stopped seeing the site's steps
  });
});
