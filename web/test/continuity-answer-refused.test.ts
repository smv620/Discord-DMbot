// The bot's "refused" answer on a heads-up reaches the panel through the real client (#3695): the change is queued, polled, and its
// failure code becomes the "refused" kind the panel shows its own words for. Nothing here is mocked above the HTTP layer.
import { describe, expect, it } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

const CLAIM = "c1".repeat(16);

describe("a refused heads-up answer through the real client", () => {
  it("rejects with the refused kind when the bot refuses the change", async () => {
    const fetchFn = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/continuity/answer")) {
        return new Response(JSON.stringify({ answer: "ans1" }), { status: 202, headers: { "Content-Type": "application/json" } });
      }
      // The answer's own read: the bot says it refused the change.
      return new Response(JSON.stringify({ state: "failed", error: "refused", code: "refused" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    const api = httpApi("/x", fetchFn as typeof fetch);
    let caught: unknown = null;
    try {
      await api.answerContinuity!("100.cmp1", { claim: CLAIM, choice: "changed" });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).kind).toBe("refused");
  });
});
