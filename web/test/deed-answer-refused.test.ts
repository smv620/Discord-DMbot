// The bot's "refused" answer on a deed reaches the card through the real client (#3591): the answer is queued, polled, and its
// failure code becomes the "refused" kind the card shows its own words for. Nothing here is mocked above the HTTP layer.
import { describe, expect, it } from "vitest";

import { ApiError, errorKinds, httpApi } from "../src/account/api";

const DEED = "a1".repeat(16);

describe("a refused deed answer through the real client", () => {
  it("maps the bot's refused code to the refused kind", () => {
    expect(errorKinds.refused).toBe("refused");
  });

  it("rejects with the refused kind when the bot refuses the answer", async () => {
    const fetchFn = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/deeds/answer")) {
        return new Response(JSON.stringify({ answer: "ans1" }), { status: 202, headers: { "Content-Type": "application/json" } });
      }
      // The answer's own read: the bot says it refused the name.
      return new Response(JSON.stringify({ state: "failed", error: "refused", code: "refused" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    const api = httpApi("/x", fetchFn as typeof fetch);
    let caught: unknown = null;
    try {
      await api.answerDeed!("100.cmp1", { deed: DEED, answer: "record" });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).kind).toBe("refused");
  });
});
