// How a line reads from the name in focus: the verb as that name says it, an arrow away from it, and nothing when
// that end has no wording (owner request after #3039).
import { describe, expect, it } from "vitest";

import type { WebEdge } from "../src/account/api";
import { readLine, readLink } from "../src/account/webReading";

const owns = { id: "1", words: "owns", secret: false, direction: "a-b" as const, back: "is owned by" };
const edge = (links: WebEdge["links"]): WebEdge => ({ a: "cerric", b: "glyph", label: "owns", more: 0, links });

describe("reading a line from the name in focus", () => {
  it("says the verb from the owner's end and the other wording from the other end, both with an arrow away", () => {
    expect(readLink(edge([owns]), owns, "cerric")).toEqual({ verb: "owns", arrow: true });
    expect(readLink(edge([owns]), owns, "glyph")).toEqual({ verb: "is owned by", arrow: true });
  });

  it("reads a connection worded from the second name the same way round", () => {
    const flipped = { ...owns, direction: "b-a" as const, words: "serves", back: "is served by" };
    expect(readLink(edge([flipped]), flipped, "glyph")).toEqual({ verb: "serves", arrow: true });
    expect(readLink(edge([flipped]), flipped, "cerric")).toEqual({ verb: "is served by", arrow: true });
  });

  it("says nothing and draws no arrowhead from an end that has no wording", () => {
    const bare = { id: "2", words: "owns", secret: false, direction: "a-b" as const };
    expect(readLink(edge([bare]), bare, "glyph")).toEqual({ verb: null, arrow: false });
    expect(readLink(edge([bare]), { ...bare, back: "" }, "glyph")).toEqual({ verb: null, arrow: false });
    expect(readLink(edge([bare]), { ...bare, back: "  " }, "glyph")).toEqual({ verb: null, arrow: false });
  });

  it("says the same words from both ends of a mutual connection, with no arrow", () => {
    const allies = { id: "3", words: "allies with", secret: false, direction: "both" as const };
    expect(readLink(edge([allies]), allies, "cerric")).toEqual({ verb: "allies with", arrow: false });
    expect(readLink(edge([allies]), allies, "glyph")).toEqual({ verb: "allies with", arrow: false });
  });

  it("reads a line of several connections: the first verb there is, how many more, and an arrow only if all go one way", () => {
    const allies = { id: "3", words: "allies with", secret: false, direction: "both" as const };
    const bare = { id: "2", words: "fears", secret: false, direction: "a-b" as const };
    expect(readLine(edge([owns, bare]), "glyph")).toEqual({ verb: "is owned by", more: 0, arrow: true }); // (bare has none from here)
    expect(readLine(edge([owns, bare]), "cerric")).toEqual({ verb: "owns", more: 1, arrow: true });
    expect(readLine(edge([owns, allies]), "cerric")).toEqual({ verb: "owns", more: 1, arrow: false });
    expect(readLine(edge([bare]), "glyph")).toEqual({ verb: null, more: 0, arrow: false });
  });
});
