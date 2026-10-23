// Every error code the import API can return for a file or a Google Doc has plain words on the import
// page, and the comment on `ImportPreviewFile.error` names them all (#2513). The codes are read from
// core's Python, so a new one fails here until the page has words for it.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { importPage } from "../src/content/import-words";

const core = (path: string): string =>
  readFileSync(fileURLToPath(new URL(`../../core/src/dmbot/${path}`, import.meta.url)), "utf8");

/** Every `NAME = "code"` constant in a Python source: in these two files each one is an error code,
 * so a new one is picked up without anyone adding its name here. */
export function constantsIn(source: string): string[] {
  return [...source.matchAll(/^[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1] as string);
}

/** The codes core can put in a file's `error`. */
function codesInCore(): Set<string> {
  const found = new Set<string>();
  // The reader's reasons: UNREADABLE = "unreadable" and the like, in the importer and the doc reader.
  for (const file of ["transcript/importer.py", "transcript/import_link.py"]) {
    for (const code of constantsIn(core(file))) found.add(code);
  }
  // The file's own: File(name, None, "not_txt", None) in the web reader.
  for (const m of core("web/imports.py").matchAll(/File\(name, None, "([a-z_]+)", None\)/g)) found.add(m[1] as string);
  return found;
}

describe("the import error codes", () => {
  const codes = codesInCore();

  it("are read from core (a guard that the reading works)", () => {
    expect(codes.size).toBeGreaterThanOrEqual(12);
    for (const known of ["unreadable", "unknown_format", "not_txt", "doc_not_shared", "repeated_link"]) expect(codes).toContain(known);
  });

  it("picks up a new constant without its name being listed (proved with a made-up one)", () => {
    expect(constantsIn('A = "a_code"  # one\nNEW_REASON = "brand_new"\nlower = "x"\nB = "has space"')).toEqual(["a_code", "brand_new"]);
  });

  it("each have plain words on the import page", () => {
    for (const code of codes) expect(importPage.fileError[code], code).toBeTruthy();
  });

  it("are all named in the comment on the preview file's error", () => {
    const api = readFileSync(fileURLToPath(new URL("../src/account/api.ts", import.meta.url)), "utf8");
    const start = api.indexOf("Why the file or doc can't be used");
    const comment = api.slice(start, api.indexOf("*/", start));
    for (const code of codes) expect(comment, code).toContain(`\`${code}\``);
  });
});
