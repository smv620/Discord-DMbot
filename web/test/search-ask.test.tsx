/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The search bar's short answer (#3855 line 2, behind SEARCH_ASK_LIVE; core #3856): the call itself (what it sends, the answer,
// a limit, every failure, a link that is not this website's), and the bar: "[... thinking ...]" with the seconds, then the
// answer, or a failure that keeps line 1, and nothing is asked on a keystroke.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { searchWords as w } from "../src/content/search-words";
import { askSite, ownPage } from "../src/search/ask";
import SearchBar from "../src/search/SearchBar";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/search-ask-switch", () => ({
  get SEARCH_ASK_LIVE() {
    return live.on;
  },
}));

const reply = (status: number, body: unknown) => async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("the call", () => {
  it("sends the question as JSON with the request header, and no cookie", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return new Response(JSON.stringify({ answer: "Yes.", link: null }), { status: 200 });
    }) as unknown as typeof fetch;
    expect(await askSite("  does it cost  ", "/api", fetcher)).toEqual({ kind: "answer", answer: "Yes.", link: null });
    expect(seen[0]!.url).toBe("/api/site/ask");
    expect(seen[0]!.init.credentials).toBe("omit");
    expect((seen[0]!.init.headers as Record<string, string>)["X-DMbot-Request"]).toBe("1");
    expect(JSON.parse(String(seen[0]!.init.body))).toEqual({ q: "does it cost" });
  });
  it("cuts a long question to the route's limit", async () => {
    let sent = "";
    const fetcher = (async (_u: string, init: RequestInit) => {
      sent = (JSON.parse(String(init.body)) as { q: string }).q;
      return new Response(JSON.stringify({ answer: "ok" }), { status: 200 });
    }) as unknown as typeof fetch;
    await askSite("x".repeat(500), "/api", fetcher);
    expect(sent).toHaveLength(300);
  });
  it("reads a limit, and treats every other trouble as a failure", async () => {
    expect(await askSite("q", "/api", reply(429, { limit: "day" }) as unknown as typeof fetch)).toEqual({ kind: "limit", limit: "day" });
    expect(await askSite("q", "/api", reply(429, { limit: "hour" }) as unknown as typeof fetch)).toEqual({ kind: "limit", limit: "hour" });
    for (const f of [reply(500, {}), reply(404, {}), reply(200, {}), reply(200, { answer: "  " }), async () => { throw new Error("offline"); }]) {
      expect(await askSite("q", "/api", f as unknown as typeof fetch)).toEqual({ kind: "failed" });
    }
    expect(await askSite("   ", "/api", reply(200, { answer: "x" }) as unknown as typeof fetch)).toEqual({ kind: "failed" });
  });
  it("uses a link only when it is a page of this website", () => {
    expect(ownPage("/pricing")).toBe("/pricing");
    expect(ownPage("/help#player-heading")).toBe("/help#player-heading");
    expect(ownPage("https://getdmbot.com/pricing")).toBe("/pricing");
    expect(ownPage("https://getdmbot.com")).toBe("/");
    for (const bad of ["https://evil.example/pricing", "//evil.example", "javascript:alert(1)", "https://getdmbot.com.evil.example/x", "", null, 3]) {
      expect(ownPage(bad), String(bad)).toBeNull();
    }
  });
});

describe("the bar", () => {
  beforeEach(() => {
    live.on = true;
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  const open = () => fireEvent.click(screen.getByRole("button", { name: w.open }));
  const type = (v: string) => fireEvent.input(screen.getByLabelText(w.label), { target: { value: v } });

  it("asks nothing while typing, only on Ask or Enter", async () => {
    const fetcher = vi.fn(reply(200, { answer: "Yes." }));
    vi.stubGlobal("fetch", fetcher);
    render(<SearchBar />);
    open();
    type("how much does it cost");
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByLabelText(w.label), { key: "Enter" });
    expect(await screen.findByText("Yes.")).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("shows thinking with the seconds, then 'Still thinking' after 20, then the answer; line 1 stays", async () => {
    let finish: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", () => new Promise<Response>((go) => (finish = go)));
    render(<SearchBar />);
    open();
    type("how much does it cost");
    fireEvent.click(screen.getByRole("button", { name: w.ask }));
    expect(await screen.findByText(w.thinking, { exact: false })).toBeTruthy();
    expect(screen.queryByText(w.stillThinking)).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(21_000);
    });
    expect(screen.getByText(w.stillThinking)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Prices" })).toBeTruthy(); // (line 1 is unchanged while waiting)
    await act(async () => finish(new Response(JSON.stringify({ answer: "From 0 to 12 dollars.", link: "/pricing" }), { status: 200 })));
    expect(await screen.findByText("From 0 to 12 dollars.", { exact: false })).toBeTruthy();
    expect(screen.queryByText(w.thinking, { exact: false })).toBeNull();
    expect(screen.getAllByRole("link", { name: "/pricing" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Prices" })).toBeTruthy();
  });
  it("says it can't answer when it fails, and keeps line 1", async () => {
    vi.stubGlobal("fetch", reply(503, {}));
    render(<SearchBar />);
    open();
    type("how much does it cost");
    fireEvent.click(screen.getByRole("button", { name: w.ask }));
    expect(await screen.findByText(w.cant)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Prices" })).toBeTruthy();
  });
  it("says what a limit means", async () => {
    vi.stubGlobal("fetch", reply(429, { limit: "day" }));
    render(<SearchBar />);
    open();
    type("anything");
    fireEvent.click(screen.getByRole("button", { name: w.ask }));
    expect(await screen.findByText(w.limit.day)).toBeTruthy();
  });
  it("clears an old answer when the words change, and drops a pending one on close", async () => {
    vi.stubGlobal("fetch", reply(200, { answer: "Old answer." }));
    render(<SearchBar />);
    open();
    type("one");
    fireEvent.click(screen.getByRole("button", { name: w.ask }));
    await screen.findByText("Old answer.");
    type("two");
    expect(screen.queryByText("Old answer.")).toBeNull();
  });
  it("has no Ask button while the switch is off", () => {
    live.on = false;
    render(<SearchBar />);
    open();
    expect(screen.queryByRole("button", { name: w.ask })).toBeNull();
  });
});
