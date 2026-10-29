// The recap page's choices use the DM screen panel's own words (#1468): each label and each "Saved."
// line is checked against the bot's code, like Help's button names, so the site and the panel cannot
// drift apart.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { choiceWords as words } from "../src/content/recap-choices";

const botDir = fileURLToPath(new URL("../../core/src/dmbot/", import.meta.url));
const panel = readFileSync(join(botDir, "dm_screen/recap_panel.py"), "utf8");

describe("the words are the panel's own", () => {
  it("has every label and every Saved line in the bot's code", () => {
    for (const label of Object.values(words.labels)) expect(panel, label).toContain(label);
    for (const pair of Object.values(words.saved))
      for (const line of Object.values(pair)) expect(panel, line).toContain(line);
    expect(panel).toContain(words.emailOff);
  });

  it("says where the same choices are on Discord, and the button it names exists", () => {
    expect(words.sameOnDiscord).toBe(
      "The same choices are in Discord: \u{1F464} DMbot Main Menu \u2192 \u2699\ufe0f Settings, in your DM Bot channel.",
    );
    // The menu it names is the bot's own (#2289, #2333).
    expect(readFileSync(join(botDir, "dm_screen/dm_bot_panel.py"), "utf8")).toContain("DMbot Main Menu");
    expect(panel).toContain('label="Recaps"');
    expect(panel).toContain("\u2699\ufe0f Settings");
  });
});
