// Where a connection came from (#2946, website half #3551): the words for each kind, nothing for an unknown or
// missing source, and the pretend API never gives a player a label.
import { describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import { webPage as w } from "../src/content/web-words";

const REF = "100.cmp1";

describe("the words for where a connection came from", () => {
  it("says each kind in its own words", () => {
    expect(w.sourceLine({ type: "session", n: 3 })).toBe("From session 3");
    expect(w.sourceLine({ type: "upload", label: "Notes on the Vale" })).toBe("From your upload: Notes on the Vale");
    expect(w.sourceLine({ type: "dm" })).toBe("Added by you");
  });
  it("shows nothing for an unknown or missing source, or one it does not know", () => {
    expect(w.sourceLine({ type: "unknown" })).toBeNull();
    expect(w.sourceLine(undefined)).toBeNull();
    expect(w.sourceLine({ type: "somethingnew" })).toBeNull();
    expect(w.sourceLine({ type: "upload" })).toBeNull();
  });
});

describe("the pretend API", () => {
  it("gives the DM all four kinds", async () => {
    const g = await mockApi("table").webGraph!(REF, { center: null, hops: 2, view: "full" });
    const types = new Set(g.edges.flatMap((e) => e.links).map((l) => l.source?.type));
    expect(types).toEqual(new Set(["session", "upload", "dm", "unknown"]));
  });
  it("gives a player only a session number or unknown, never a label", async () => {
    const g = await mockApi("table").webGraph!(REF, { center: null, hops: 2, view: "players" });
    const sources = g.edges.flatMap((e) => e.links).map((l) => l.source);
    expect(sources.length).toBeGreaterThan(0);
    for (const s of sources) expect(["session", "unknown"]).toContain(s?.type);
    expect(JSON.stringify(sources)).not.toContain("Vale");
  });
});
