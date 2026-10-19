// Every rule the Help text names as an example is in the rules data the bot reads (#1962). A DM who
// types "Bag of Holdings" because we wrote it that way gets "not found" on the first try; this is
// the same guard button-labels.test.ts gives the button names (#1893).
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { fightRuleSteps, magicItemRuleSteps, ruleExamples, ruleKindSteps } from "../src/content/help";
import { held } from "../src/content/whats-new";

const dataDir = fileURLToPath(new URL("../../core/src/dmbot/rules/data/", import.meta.url));

interface Row {
  name: string;
  kind: string;
  class?: string;
}

// The shape comes from core/src/dmbot/rules/index.py `load_folder`: a file has a top-level `kind`
// and `entries`; an entry may carry its own `kind` (equipment and origins do), and a class feature
// has a `class`. Only those three fields are read.
function rows(): Row[] {
  const out: Row[] = [];
  for (const edition of readdirSync(dataDir, { withFileTypes: true }).filter((d) => d.isDirectory())) {
    for (const file of readdirSync(join(dataDir, edition.name)).filter((f) => f.endsWith(".json"))) {
      const data = JSON.parse(readFileSync(join(dataDir, edition.name, file), "utf8"));
      for (const e of data.entries ?? []) {
        out.push({ name: String(e.name), kind: String(e.kind ?? data.kind), class: e.class });
      }
    }
  }
  return out;
}

// The bot's own key (core/src/dmbot/rules/index.py `normalize`): case, apostrophes (curly or
// straight) and punctuation ignored, and a leading "a" or "an" dropped from what the DM typed.
const key = (name: string): string =>
  name
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[’‘ʼ'`]/g, "")
    .replace(/[^0-9a-z]+/g, " ")
    .trim()
    .replace(/^an? /, "");

const kindOf: Record<string, string[]> = {
  rule: ["rule"],
  feature: ["feature"],
  item: ["item"],
  weapon: ["weapon"],
  armor: ["armor"],
  tool: ["tool"],
  gear: ["gear"],
  feat: ["feat"],
  species: ["species"],
  background: ["background"],
  monster: ["monster"],
};

// The test checks the name and kind only, not the edition: "Orc (creature)" is in the 2014 data alone,
// so the bot tags it [Legacy 2014]. That is fine for an example; the name just has to be real.
const all = rows();

/** The example's entry name: the typed words, minus a "(creature)" style bracket and a class prefix. */
function wanted(example: (typeof ruleExamples)[number]): string {
  return key(example.name ?? example.text.replace(/\s*\([^)]*\)\s*$/, ""));
}

/** An example that is not in the data, or null: the closest real names of that kind for the message. */
function problem(example: (typeof ruleExamples)[number], data: Row[] = all): string | null {
  const kinds = kindOf[example.kind] ?? [example.kind];
  const ofKind = data.filter((r) => kinds.includes(r.kind));
  const hit = ofKind.some(
    (r) => key(r.name) === wanted(example) && (!example.class || key(r.class ?? "") === key(example.class)),
  );
  if (hit) return null;
  const first = wanted(example).split(" ")[0] ?? "";
  const close = [...new Set(ofKind.filter((r) => key(r.name).includes(first)).map((r) => r.name))].slice(0, 5);
  return `Help names '${example.text}' but the rules data has no ${example.kind} by that name${
    close.length ? `; the closest are: ${close.join(", ")}` : ""
  }`;
}

describe("rule examples in Help", () => {
  it("reads the bot's rules data", () => {
    expect(all.length).toBeGreaterThan(1000);
    expect(new Set(all.map((r) => r.kind))).toContain("armor");
  });

  it("names every example in the Help step or the held entry, so the list cannot drift from the words", () => {
    const words = [
      ...[...ruleKindSteps, ...fightRuleSteps, ...magicItemRuleSteps].map((s) => s.text),
      ...["Look up any rule", "Look up fight difficulty rules"].map((t) => held.find((h) => h.title === t)?.what ?? ""),
    ].join(" ");
    for (const example of ruleExamples) {
      expect(words, `${example.text} is in the words`).toContain(example.text);
    }
  });

  it("finds every example in the data, by the bot's own matching", () => {
    expect(ruleExamples.map((e) => problem(e)).filter(Boolean)).toEqual([]);
  });

  it("reports a misspelt example with the closest real names", () => {
    const bad = problem({ text: "Bag of Holdings", kind: "item" });
    expect(bad).toContain("Help names 'Bag of Holdings'");
    expect(bad).toContain("Bag of Holding");
    expect(problem({ text: "Thieves Tools", kind: "tool" })).toBeNull(); // punctuation is ignored, as the bot does
    expect(problem({ text: "a longsword", kind: "weapon", name: "a longsword" })).toBeNull(); // a leading "a" is ignored
    expect(problem({ text: "rogue evasion", kind: "feature", name: "Evasion", class: "Wizard" })).not.toBeNull();
  });
});
