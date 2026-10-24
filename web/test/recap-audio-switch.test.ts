// The narrated-recaps add-on must not be switched on without its price (#1390): a card that offers
// something and says nothing of the cost is not honest. The real constants, no mocks.
import { describe, expect, it } from "vitest";

import { RECAP_AUDIO_PRICE_CENTS } from "../src/content/recap-audio";
import { plansAnswer } from "../src/content/pricing";
import { RECAP_AUDIO_LIVE, RECAPS_LIVE } from "../src/content/recap-words";

describe("the add-on's switch", () => {
  it("is on only with recaps, and never offers the add-on without a price", () => {
    expect(!RECAP_AUDIO_LIVE || RECAPS_LIVE).toBe(true);
    if (RECAP_AUDIO_PRICE_CENTS === null) {
      // Narration is on (#2617) while the owner has not set the add-on's price (#1385): the words say only
      // that Guild and Pro include it, and nothing about buying it.
      const answer = plansAnswer("writes down what is said", "Try It");
      expect(answer).toContain("Guild and Pro include them.");
      expect(answer).not.toMatch(/add them|by the month|\$/);
    }
  });

  it("keeps 'Every plan has everything' true: the FAQ calls the add-on an extra", () => {
    const answer = plansAnswer("writes down what is said", "Try It");
    expect(answer).toContain("Every plan has the same DMbot.");
    expect(answer).toContain("are an extra: Guild and Pro include them");
    expect(answer).not.toMatch(/stem|render/i);
  });
});
