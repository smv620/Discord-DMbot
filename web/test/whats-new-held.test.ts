// A held What's new reason must not say "dev1 deploys NNNN" for a migration that is already on the
// server (#1888): a stale reason sends the owner and dev1 to do a step that is done. The server's
// last migration comes from docs/testing-status.log ("LIVE NOW"), the same place the go-live map reads.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { held } from "../src/content/whats-new";

const statusLog = fileURLToPath(new URL("../../docs/testing-status.log", import.meta.url));

/** The highest migration number named in the "LIVE NOW" part of the status log, or 0 if none. */
export function highestLiveMigration(log: string): number {
  const start = log.indexOf("LIVE NOW");
  if (start < 0) return 0;
  const end = log.indexOf("\nNEXT TEST:", start);
  const section = log.slice(start, end < 0 ? undefined : end);
  const numbers = [...section.matchAll(/\b(0\d{3})\b/g)].map((m) => Number(m[1]));
  return numbers.length ? Math.max(...numbers) : 0;
}

/** One plain sentence for each held entry whose reason still waits for a migration that is live. */
export function staleHeldReasons(items: { title: string; reason: string }[], live: number): string[] {
  const problems: string[] = [];
  for (const item of items) {
    for (const m of item.reason.matchAll(/deploys (\d{4})/g)) {
      if (Number(m[1]) <= live) {
        problems.push(
          `"${item.title}" says it waits for dev1 to deploy ${m[1]}, but the server already has migrations up to ${String(live).padStart(4, "0")}. ` +
            `Change the reason to "Deployed <date> (<deploy sha>); held until the owner's live check is recorded in docs/testing-history.log."`,
        );
      }
    }
  }
  return problems;
}

describe("held What's new reasons", () => {
  it("reads the server's last migration from the status log", () => {
    expect(highestLiveMigration(readFileSync(statusLog, "utf8"))).toBeGreaterThanOrEqual(130);
  });

  it("none waits for a deploy of a migration that is already live", () => {
    const live = highestLiveMigration(readFileSync(statusLog, "utf8"));
    expect(staleHeldReasons(held, live)).toEqual([]);
  });

  it("names the entry and what to change when one is stale", () => {
    const stale = [{ title: "Watch the story", reason: "Merged (#1730); held until dev1 deploys 0125 and the owner's live check is recorded." }];
    const problems = staleHeldReasons(stale, 130);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("Watch the story");
    expect(problems[0]).toContain("0125");
    expect(problems[0]).toContain("Change the reason");
    expect(staleHeldReasons(stale, 124)).toEqual([]); // not yet live: leave it alone
  });
});
