// @vitest-environment happy-dom
// The Story Graph's size and peek calls over the real API client (#2405): the size is asked
// for the view the page is showing, and a refused peek reads as a plain failure.
import { describe, expect, it } from "vitest";

import { ApiError, httpApi } from "../src/account/api";

const seen: string[] = [];
const answering = (status: number, body: unknown) =>
  httpApi("https://api.example", (async (url: string) => {
    seen.push(String(url));
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch);

describe("the web's size", () => {
  it.each(["full", "players"] as const)("is asked for the %s view", async (view) => {
    seen.length = 0;
    await answering(200, { names: 3 }).webSize!("100.cmp1", { center: "n1", hops: 2, view });
    expect(seen).toHaveLength(1);
    expect(new URL(seen[0]!).searchParams.get("view")).toBe(view);
  });

  it("names no view when none is asked for", async () => {
    seen.length = 0;
    await answering(200, { names: 3 }).webSize!("100.cmp1", { center: null, hops: 2 });
    expect(new URL(seen[0]!).searchParams.has("view")).toBe(false);
  });
});

describe("a refused peek", () => {
  it("reads a 400 bad_visit as a plain failure, not a reason", async () => {
    const error = await answering(400, { error: "bad_visit" })
      .webPeek!("100.cmp1.web-player")
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("server");
  });
});
