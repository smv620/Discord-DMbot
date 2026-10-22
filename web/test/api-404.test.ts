// @vitest-environment happy-dom
// A 404 with no error code reads as "not found", not as an outage (#2306).
import { describe, expect, it } from "vitest";

import { httpApi, SIGNED_OUT_EVENT, type AccountApi } from "../src/account/api";

const answering = (status: number, body: string | null, type = "application/json"): AccountApi =>
  httpApi("https://api.example", (async () =>
    new Response(body, { status, headers: body === null ? {} : { "Content-Type": type } })) as typeof fetch);

// Any call that goes through the shared `call()`; the second argument is irrelevant to the answer.
const ask = (api: AccountApi): Promise<unknown> => api.allNames!("100.cmp1", { q: "", kind: "", sort: "name", page: 1 });

describe("a 404 with no code", () => {
  it("is 'not found' for an empty body", async () => {
    await expect(ask(answering(404, null))).rejects.toMatchObject({ kind: "not-found" });
  });

  it("is 'not found' for a body that isn't JSON (a proxy's own page)", async () => {
    await expect(ask(answering(404, "<html>Not found</html>", "text/html"))).rejects.toMatchObject({
      kind: "not-found",
    });
  });

  it("is 'not found' for JSON without an error code", async () => {
    await expect(ask(answering(404, "{}"))).rejects.toMatchObject({ kind: "not-found" });
  });

  it("is 'not found' for FastAPI's own answer to a route it doesn't have", async () => {
    await expect(ask(answering(404, JSON.stringify({ detail: "Not Found" })))).rejects.toMatchObject({
      kind: "not-found",
    });
  });

  it("keeps what a coded 404 says", async () => {
    await expect(ask(answering(404, JSON.stringify({ error: "bad_page" })))).rejects.toMatchObject({
      kind: "bad-page",
    });
  });
});

describe("an outage still reads as one", () => {
  it.each([500, 502, 503, 504])("%i is a server problem, not 'not found'", async (status) => {
    await expect(ask(answering(status, null))).rejects.toMatchObject({ kind: "server" });
  });

  it("a network failure is a network problem", async () => {
    const api = httpApi("https://api.example", (async () => {
      throw new TypeError("failed to fetch");
    }) as typeof fetch);
    await expect(ask(api)).rejects.toMatchObject({ kind: "network" });
  });

  it("signs the page out only on a 401", async () => {
    let fired = 0;
    const count = (): void => void (fired += 1);
    window.addEventListener(SIGNED_OUT_EVENT, count);
    for (const status of [403, 404, 500]) await ask(answering(status, null)).catch(() => undefined);
    expect(fired).toBe(0);
    await ask(answering(401, null)).catch(() => undefined);
    expect(fired).toBe(1);
    window.removeEventListener(SIGNED_OUT_EVENT, count);
  });

  it("403 stays 'not allowed' and 401 stays 'signed out'", async () => {
    await expect(ask(answering(403, null))).rejects.toMatchObject({ kind: "not-allowed" });
    await expect(ask(answering(401, null))).rejects.toMatchObject({ kind: "signed-out" });
  });
});
