// World notes' words for a culture and who lives here (#5045): the layer hints, and each row's line in the panel's words, a named
// culture by its name, and "a culture of your own" for a named culture the page cannot name.
import { describe, expect, it } from "vitest";

import { worldNotesWords as w } from "../src/content/world-notes-words";

const place = (over: Partial<Parameters<typeof w.place>[0]>) => w.place({ name: "Saltmarsh", layer: "culture", value: "", words: "", how: null, ruler: null, ...over });

describe("World notes' culture lines", () => {
  it("the layer hints are Culture and Who lives here", () => {
    expect(w.kindHint.culture).toBe("Culture");
    expect(w.kindHint.population).toBe("Who lives here");
  });

  it("a culture row is named, with its strength", () => {
    expect(place({ value: "dwarf", how: "strong" })).toBe("Saltmarsh: dwarven (strongly)");
  });

  it("a named culture shows its name", () => {
    expect(place({ value: "", culture: "Illuskan", how: "some" })).toBe("Saltmarsh: Illuskan (somewhat)");
  });

  it("the DM's own words show as they are", () => {
    expect(place({ value: "other", words: "Frost folk", how: "" })).toBe("Saltmarsh: Frost folk");
  });

  it("a named culture the page cannot name reads as a culture of your own", () => {
    expect(place({ value: "", culture: null, how: "strong" })).toBe("Saltmarsh: a culture of your own (strongly)");
  });

  it("who lives here reads with its share", () => {
    expect(place({ layer: "population", value: "halfling", how: "most" })).toBe("Saltmarsh: mostly halflings");
    expect(place({ layer: "population", value: "human", how: "" })).toBe("Saltmarsh: humans");
  });
});
