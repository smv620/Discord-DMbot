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
  const wait = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

  it("uses the owner's exact words", () => {
    expect(w.cant).toBe("DMbot can't answer right now. Try again in a little while.");
    expect(w.limit.hour).toBe("Too many requests to process, try again in about an hour.");
    expect(w.limit.day).toBe("Too many requests to process, try again tomorrow.");
  });
  it("asks once after a 600 ms pause, with no button pressed", async () => {
    const fetcher = vi.fn(reply(200, { answer: "Yes." }));
    vi.stubGlobal("fetch", fetcher);
    render(<SearchBar />);
    open();
    type("how much does it cost");
    await wait(500);
    expect(fetcher).not.toHaveBeenCalled();
    await wait(200);
    expect(await screen.findByText("Yes.")).toBeTruthy();
    await wait(2000);
    expect(fetcher).toHaveBeenCalledTimes(1); // (the same words are not asked twice)
  });
  it("does not ask while typing carries on inside 600 ms", async () => {
    const fetcher = vi.fn(reply(200, { answer: "Yes." }));
    vi.stubGlobal("fetch", fetcher);
    render(<SearchBar />);
    open();
    for (const v of ["h", "ho", "how", "how m"]) {
      type(v);
      await wait(400);
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("drops the earlier call when the words change, and asks again", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", (_u: string, init: RequestInit) => {
      signals.push(init.signal as AbortSignal);
      return new Promise<Response>(() => {});
    });
    render(<SearchBar />);
    open();
    type("first question");
    await wait(700);
    type("second question");
    await wait(700);
    expect(signals).toHaveLength(2);
    expect(signals[0]!.aborted).toBe(true);
    expect(signals[1]!.aborted).toBe(false);
  });
  it("asks at once on Enter, and Ask does the same", async () => {
    const fetcher = vi.fn(reply(200, { answer: "Yes." }));
    vi.stubGlobal("fetch", fetcher);
    render(<SearchBar />);
    open();
    type("how much does it cost");
    fireEvent.keyDown(screen.getByLabelText(w.label), { key: "Enter" });
    expect(await screen.findByText("Yes.")).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("shows thinking at once, the seconds from 3 s, and the cant line at 12 s with the call dropped", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", (_u: string, init: RequestInit) => {
      signal = init.signal as AbortSignal;
      return new Promise<Response>(() => {});
    });
    render(<SearchBar />);
    open();
    type("how much does it cost");
    fireEvent.keyDown(screen.getByLabelText(w.label), { key: "Enter" });
    expect(await screen.findByText(w.thinking)).toBeTruthy();
    await wait(2000);
    expect(screen.getByText(w.thinking)).toBeTruthy(); // (no seconds yet)
    await wait(2000);
    expect(screen.getByText(w.thinkingFor(4))).toBeTruthy();
    expect(screen.getByRole("link", { name: "Prices" })).toBeTruthy(); // (line 1 stays)
    await wait(9000);
    expect(screen.getByText(w.cant)).toBeTruthy();
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText("Still thinking…", { exact: false })).toBeNull();
  });
  it("shows the answer with at most one link to this site, and reads it as text", async () => {
    vi.stubGlobal("fetch", reply(200, { answer: "See <b>prices</b>.", link: "/pricing" }));
    render(<SearchBar />);
    open();
    type("how much does it cost");
    fireEvent.keyDown(screen.getByLabelText(w.label), { key: "Enter" });
    expect(await screen.findByText("See <b>prices</b>.", { exact: false })).toBeTruthy();
    expect(document.querySelector(".site-search-answer b")).toBeNull();
    expect(screen.getAllByRole("link", { name: "/pricing" })).toHaveLength(1);
  });
  it("says it can't answer when the call fails, and keeps line 1; a retry is allowed", async () => {
    const fetcher = vi.fn(reply(503, {}));
    vi.stubGlobal("fetch", fetcher);
    render(<SearchBar />);
    open();
    type("how much does it cost");
    fireEvent.keyDown(screen.getByLabelText(w.label), { key: "Enter" });
    expect(await screen.findByText(w.cant)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Prices" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.ask }));
    await wait(10);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("says what a limit means", async () => {
    vi.stubGlobal("fetch", reply(429, { limit: "day" }));
    render(<SearchBar />);
    open();
    type("anything");
    fireEvent.keyDown(screen.getByLabelText(w.label), { key: "Enter" });
    expect(await screen.findByText(w.limit.day)).toBeTruthy();
  });
  it("asks nothing and shows no Ask button while the switch is off", async () => {
    live.on = false;
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    render(<SearchBar />);
    open();
    type("how much does it cost");
    await wait(2000);
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: w.ask })).toBeNull();
  });
});
