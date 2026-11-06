// A backup's file name may carry the commit of the build that made it after "#" (#3391); the page keeps
// the "#" in the name and still names the transcripts files from the same stem.
import { describe, expect, it } from "vitest";
import { safeBackupName } from "../src/account/api";

describe("a backup's file name", () => {
  it("keeps the commit after the hash", () => {
    expect(safeBackupName("tales-2026-11-04.dmbot#bd8ae13a.json.gz", "x")).toBe("tales-2026-11-04.dmbot#bd8ae13a.json.gz");
  });

  it("still cleans out everything else", () => {
    expect(safeBackupName("a b/c?.dmbot#unknown.json.gz", "x")).toBe("a-b-c-.dmbot#unknown.json.gz");
    expect(safeBackupName("", "campaign.dmbot.json.gz")).toBe("campaign.dmbot.json.gz");
  });

  it("gives the same stem for the old ending and the new one", () => {
    const stem = (name: string): string => name.replace(/\.dmbot(#[0-9a-z]+)?\.json\.gz$/, "");
    expect(stem("tales-2026-11-04.dmbot#bd8ae13a.json.gz")).toBe("tales-2026-11-04");
    expect(stem("tales-2026-11-04.dmbot.json.gz")).toBe("tales-2026-11-04");
  });
});
