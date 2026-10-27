/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Names fixed this session" (#2645), against the shapes agreed on the issue: the list with counts and the
// spellings replaced, Undo through the bot (with the page told how many lines came back), the refusals in
// plain words, and nothing at all for anyone but the DM.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { mockApi } from "../src/account/mock";
import NamesFixed from "../src/account/NamesFixed";
import { namesFixedWords as w } from "../src/content/names-fixed-words";

afterEach(cleanup);

const REF = "100.cmp1.1791000000";
async function open(ref = REF, api: ReturnType<typeof mockApi> = mockApi("table")) {
  render(<NamesFixed api={api} recapRef={ref} />);
  return api;
}
const heading = () => screen.findByRole("heading", { name: w.heading });
const row = (name: string): HTMLElement => screen.getByText(name).closest("li") as HTMLElement;

describe("Names fixed this session", () => {
  it("lists each name with how often it was fixed and the spellings it replaced", async () => {
    await open();
    await heading();
    expect(row("Kethrandil").textContent).toContain("fixed 8 times");
    expect(row("Kethrandil").textContent).toContain("Keirandal, 5 times");
    expect(row("Kethrandil").textContent).toContain("Kethrandel, 3 times");
    expect(row("Brindlemoor").textContent).toContain("fixed once");
    expect(screen.getByText(/2 words it wasn't sure of and 1 new name/)).toBeTruthy();
  });

  it("says so when nothing was fixed", async () => {
    await open("100.cmp1.names-none.1791000000");
    await heading();
    expect(screen.getByText(w.empty)).toBeTruthy();
  });

  it("puts a name back through the bot and says how many lines came back", async () => {
    const api = await open();
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "Undo Kethrandil" }));
    await waitFor(() => expect(screen.getByText(w.putBack(8))).toBeTruthy());
    // The keyboard stays on that name's line.
    await waitFor(() => expect(document.activeElement?.getAttribute("data-row")).toBe("nf-kethrandil"));
    expect(api.calls).toContain(`undoNameFix:${REF}:nf-kethrandil`);
    expect(row("Kethrandil").textContent).toContain(w.undone);
    expect(row("Kethrandil").querySelector("button")).toBeNull();
  });

  it("says what happened when the lines were changed since", async () => {
    await open();
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "Undo Oskar Vane" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.errors["gone"]));
    // The re-read that follows must not wipe the message.
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByRole("alert").textContent).toBe(w.errors["gone"]);
  });

  it("leaves it on the list when DMbot can't write now", async () => {
    await open();
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "Undo Brindlemoor" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(w.errors["cannot-write"]));
    expect(screen.getByRole("button", { name: "Undo Brindlemoor" })).toBeTruthy();
  });

  it("does one Undo at a time, even for two presses at once", async () => {
    const api = await open();
    await heading();
    const button = screen.getByRole("button", { name: "Undo Kethrandil" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText(w.putBack(8))).toBeTruthy());
    expect(api.calls.filter((c) => c.startsWith("undoNameFix:"))).toHaveLength(1);
  });

  it("shows nothing at all to a player, not even the heading", async () => {
    const api = await open("100.cmp1.player.1791000000");
    await waitFor(() => expect(api.calls).toContain("namesFixed:100.cmp1.player.1791000000"));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.queryByText(w.lead)).toBeNull();
  });

  it("shows nothing against an API older than the list", async () => {
    const old = mockApi("table");
    delete (old as { namesFixed?: unknown }).namesFixed;
    await open(REF, old);
    expect(screen.queryByRole("heading")).toBeNull();
  });
});

// The door writes how many lines were put back as `putBack` (core PR 2709): the real client must read that word.
describe("the real client reads the Undo's answer", () => {
  it("takes putBack from the polled answer", async () => {
    const { httpApi } = await import("../src/account/api");
    const seen: string[] = [];
    const fetchMock = async (url: string, init?: { method?: string }): Promise<Response> => {
      seen.push(`${init?.method ?? "GET"} ${url}`);
      const body = init?.method === "POST" ? { answer: "ans1" } : { state: "done", error: null, putBack: 7, waits: { stage: "done", waitedSeconds: 1, lastMovedAt: 1, stalled: false } };
      return new Response(JSON.stringify(body), { status: init?.method === "POST" ? 202 : 200, headers: { "content-type": "application/json" } });
    };
    const original = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      const api = httpApi("https://api.example.test");
      const got = await api.undoNameFix!(REF, "12");
      expect(got.putBack).toBe(7);
      expect(seen[0]).toContain(`/me/recaps/${REF}/names-fixed/12/undo`);
      expect(seen[1]).toContain("/me/campaigns/100.cmp1/web/answers/ans1");
    } finally {
      globalThis.fetch = original;
    }
  });

  // The door refuses an Undo made too soon after the last with 429 and `too_soon`: the page must say the
  // "bit quick" sentence, not the generic failure.
  it("says the 'a bit quick' sentence for a 429 too_soon", async () => {
    const { httpApi } = await import("../src/account/api");
    const original = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: "too_soon" }), { status: 429, headers: { "content-type": "application/json" } })) as unknown as typeof fetch;
    try {
      const api = httpApi("https://api.example.test");
      const error = await api.undoNameFix!(REF, "12").catch((e: unknown) => e);
      expect((error as { kind?: string }).kind).toBe("too-soon");
      expect(w.errors["too-soon"]).toBe("That was a bit quick. Wait a few seconds, then try again.");
    } finally {
      globalThis.fetch = original;
    }
  });
});
