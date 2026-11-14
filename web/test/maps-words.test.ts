// The Maps page's words (#4347): every string is a plain, non-empty line, the functions read as the issue quotes them, and the strings
// Discord already shows are copied from core's words.py, letter for letter.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { mapsWords as w } from "../src/content/maps-words";

// Sample arguments for each function in the words, by name. A function with no entry fails the test below.
const samples: Record<string, unknown[]> = {
  dmTitle: ["Ana"],
  answeredBy: ["Ana", true],
  addedBy: ["Ana", "website"],
  sending: [5],
  saving: [5],
  lastMoved: [12],
  stalled: [30, "20 seconds"],
  badFile: [10],
};

describe("maps words", () => {
  it("every string is a non-empty line with no 'audience', 'layer', 'feature', 'calibration' or 'status'", () => {
    for (const [name, value] of Object.entries(w)) {
      let text: string;
      if (typeof value === "function") {
        expect(samples[name], `${name} needs a sample`).toBeDefined();
        text = (value as (...args: unknown[]) => string)(...samples[name]!);
      } else {
        text = value;
      }
      expect(typeof text, name).toBe("string");
      expect(text.trim(), name).not.toBe("");
      expect(text, name).not.toContain("\n");
      expect(text, name).not.toMatch(/\b(audience|layer|feature|calibration|status)\b/i);
    }
  });

  it("the function strings read as quoted", () => {
    expect(w.addedBy("Ana", "website")).toBe("Added by Ana on the website");
    expect(w.addedBy("Ana", "discord")).toBe("Added by Ana in Discord");
    expect(w.sending(5)).toBe("Sending your map… 5 seconds so far.");
    expect(w.saving(5)).toBe("Saving your map… 5 seconds so far.");
    expect(w.lastMoved(12)).toBe("Last moved 12 seconds ago.");
    expect(w.stalled(30, "20 seconds")).toBe("Still working after 30 seconds, but nothing has moved for 20 seconds. Give it a minute. If it stops, check the picture and try again.");
    expect(w.badFile(10)).toBe("That didn't work. Check the picture is a PNG or JPG under 10 MB and try again.");
    expect(w.dmTitle("Ana")).toBe("A map from Ana: use it?");
    expect(w.answeredBy("Ana", true)).toBe("Ana already answered: yes.");
    expect(w.answeredBy("Ana", false)).toBe("Ana already answered: no.");
  });

  it("each string Discord shows is copied from core's words.py, letter for letter", () => {
    const py = readFileSync(fileURLToPath(new URL("../../core/src/dmbot/maps/words.py", import.meta.url)), "utf8");
    // Plain strings: the whole literal, in its quotes, as core writes it.
    const literals = [
      w.question,
      w.yesLabel,
      w.noLabel,
      w.useYes,
      w.useNo,
      w.removeIt,
      w.undoLabel,
      w.undone,
      w.undoFailed,
      w.gone,
      w.failedShared,
      w.full,
      w.dmOnly,
      w.takeBack,
      w.takenBack,
      w.takeBackUndoFailed,
      w.doneYes,
      w.doneNo,
      w.doneRemoved,
    ];
    for (const text of literals) expect(py, text).toContain(`"${text}"`);
    // Built strings: each of their parts is core's, as its own literal or template.
    expect(py).toContain('"DMbot can\'t check who made a picture or what you\'ve agreed with them,"');
    expect(py).toContain('f"{_CANT_CHECK} so it goes by your answer."');
    expect(py).toContain('f"A map from {player}: use it?"');
    expect(py).toContain('f"{dm} already answered: {\'yes\' if yes else \'no\'}."');
  });
});
