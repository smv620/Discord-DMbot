// The narrated-recaps add-on must not be switched on without its price (#1390): a card that offers
// something and says nothing of the cost is not honest. The real constants, no mocks.
import { describe, expect, it } from "vitest";

import { RECAP_AUDIO_PRICE_CENTS } from "../src/content/recap-audio";
import { plansAnswer } from "../src/content/pricing";
import { RECAP_AUDIO_LIVE, RECAPS_LIVE } from "../src/content/recap-words";

describe("the add-on's switch", () => {
  it("is off today, and only ever on with recaps and a price", () => {
    expect(RECAP_AUDIO_LIVE).toBe(false);
    expect(
      !RECAP_AUDIO_LIVE || (RECAPS_LIVE && RECAP_AUDIO_PRICE_CENTS !== null),
    ).toBe(true);
  });

  it("keeps 'Every plan has everything' true: the FAQ calls the add-on an extra", () => {
    const answer = plansAnswer("writes down what is said", "Try It");
    expect(answer).toContain("Every plan has the same DMbot.");
    expect(answer).toContain("are an extra: Guild and Pro include them");
    expect(answer).not.toMatch(/stem|render/i);
  });
});
