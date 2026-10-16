// The transcript pages' notes are the bot's own (#1501): word for word from export.py, so the
// website never says anything different from the file Discord gives. Node-env on purpose: it reads
// the bot's source.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { cleanedNote, heardNote, text } from "../src/content/transcripts";

const botDir = fileURLToPath(new URL("../../core/src/dmbot/", import.meta.url));
const read = (file: string) => readFileSync(botDir + file, "utf8");

/** A Python string made of adjacent literals ("..." "..."), as the value it has. */
function pythonString(source: string, name: string): string {
  const start = source.indexOf(`${name} = (`);
  expect(start, `${name} in export.py`).toBeGreaterThan(-1);
  const body = source.slice(start + name.length + 4, source.indexOf("\n)\n", start));
  return [...body.matchAll(/(["'])((?:\\.|(?!\1).)*)\1/g)]
    .map((m) => (m[2] ?? "").replace(/\\"/g, '"').replace(/\\'/g, "'"))
    .join("");
}

describe("the transcript notes", () => {
  const export_ = read("transcript/export.py");

  it("are the bot's cleaned and as-heard notes, word for word", () => {
    expect(cleanedNote).toBe(pythonString(export_, "CLEANED_NOTE"));
    expect(heardNote).toBe(pythonString(export_, "AS_HEARD_NOTE"));
  });

  it("say who was recorded, as the file does", () => {
    for (const note of [cleanedNote, heardNote]) expect(note).toContain("Only people who agreed were recorded.");
  });

  it("explain the website's speaker numbers in the API's own words", () => {
    expect(text.names).toBe(pythonString(read("web/transcripts.py"), "NAMES_NOTE"));
  });
});
