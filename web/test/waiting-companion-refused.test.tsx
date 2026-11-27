/** @jsxImportSource preact */
// @vitest-environment happy-dom
// A refused keep of a companion (#5348; core #5346): the page says why, in the store's own sentence, and what to press.
// Other refusals keep their own words, and the sentence is never in the title.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import WebWaiting from "../src/account/WebWaiting";
import { waitingWords as w } from "../src/content/web-waiting";

afterEach(() => cleanup());

const REF = "100.cmp1.companion";
const SENTENCE = "Only a character who isn't a player character can be a companion.";
const LINE = `DMbot can't keep that one. ${SENTENCE} Press ❌ Leave it out to move on.`;

async function page(api: AccountApi) {
  render(<WebWaiting api={api} campaignRef={REF} />);
  await screen.findByRole("heading", { name: w.heading });
}

describe("a refused companion keep", () => {
  it("says why and what to press, with the store's sentence as given", async () => {
    const api = mockApi("table");
    await page(api);
    const keep = screen.getAllByRole("button", { name: w.confirm })[0]!;
    fireEvent.click(keep);
    expect(await screen.findByText(LINE)).toBeTruthy();
  });

  it("the item stays, and Not right still works", async () => {
    const api = mockApi("table");
    await page(api);
    fireEvent.click(screen.getAllByRole("button", { name: w.confirm })[0]!);
    await screen.findByText(LINE);
    expect(screen.getByText("Glyph travels with Cerric", { exact: false })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: w.reject }).length).toBeGreaterThan(0);
  });

  it("a companion error with no sentence says the general words", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => {
      throw new ApiError("companion");
    };
    await page(api);
    fireEvent.click(screen.getAllByRole("button", { name: w.confirm })[0]!);
    expect(await screen.findByText(w.errors.other!)).toBeTruthy();
    expect(screen.queryByText(/can't keep that one/)).toBeNull();
  });

  it("another refusal still says its own words, not the companion line", async () => {
    const api = mockApi("table");
    api.answerWaiting = async () => {
      throw Object.assign(new ApiError("gone"), { note: "Some other refusal" });
    };
    await page(api);
    fireEvent.click(screen.getAllByRole("button", { name: w.confirm })[0]!);
    expect(await screen.findByText(w.errors.gone!)).toBeTruthy();
    expect(screen.queryByText(/can't keep that one/)).toBeNull();
  });

});

// The wire path (#5348): a refused keep answered by the real client copies the store's sentence onto the error, and
// a companion answer with no sentence stays the general error.
describe("a companion answer through the real client", () => {
  it("copies why onto the error", async () => {
    const fetchFn = async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ answer: "ans1" }), { status: 202, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify({ state: "failed", error: "companion", why: SENTENCE }), { status: 200, headers: { "Content-Type": "application/json" } });
    };
    const api = httpApi("/x", fetchFn as typeof fetch);
    let caught: ApiError | null = null;
    try {
      await api.answerWaiting!(REF, { kind: "claim", id: "c-companion", action: "confirm" } as never);
    } catch (error) {
      caught = error as ApiError;
    }
    expect(caught?.kind).toBe("companion");
    expect(caught?.note).toBe(SENTENCE);
  });

  it("without a sentence there is no note", async () => {
    const fetchFn = async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ answer: "ans1" }), { status: 202, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify({ state: "failed", error: "companion" }), { status: 200, headers: { "Content-Type": "application/json" } });
    };
    const api = httpApi("/x", fetchFn as typeof fetch);
    let caught: ApiError | null = null;
    try {
      await api.answerWaiting!(REF, { kind: "claim", id: "c-companion", action: "confirm" } as never);
    } catch (error) {
      caught = error as ApiError;
    }
    expect(caught?.kind).toBe("companion");
    expect(caught?.note).toBeUndefined();
  });
});
