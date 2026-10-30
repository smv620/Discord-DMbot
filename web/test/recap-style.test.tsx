/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Recap style on the website (#2985): the examples and notes, each change queued and followed to its end.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapStyleView from "../src/account/RecapStyle";
import { recapStylePage as words, RECAP_STYLE_LIVE, STYLE_EXAMPLE_WORDS } from "../src/content/recap-style-words";
import { entries, held } from "../src/content/whats-new";

const PATH = "/account/recap-style/100.cmp1";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the words", () => {
  it("hold the What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === "Choose how your recaps sound")).toBe(!RECAP_STYLE_LIVE);
    expect(entries.some((e) => e.title === "Choose how your recaps sound")).toBe(RECAP_STYLE_LIVE);
  });
});

describe("Recap style", () => {
  it("shows the DM's own examples and notes", async () => {
    render(<RecapStyleView api={mockApi("table")} path={PATH} />);
    expect(await screen.findByText(words.usingOwn(1))).toBeTruthy();
    expect(screen.getByText(/silver lock clicked/)).toBeTruthy();
    expect(screen.getByText(words.notesNone)).toBeTruthy();
  });

  it("sends a new example and follows it until it is done, then shows it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.exampleLabel), { target: { value: "A second recap with its own voice." } });
    fireEvent.click(screen.getByRole("button", { name: words.addExample }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("changeRecapStyle:100.cmp1:add_example:"))).toBe(true));
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(await screen.findByText(words.usingOwn(2))).toBeTruthy();
    expect(screen.getByText(/second recap with its own voice/)).toBeTruthy();
  });

  it("adds a note and removes an example", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.noteLabel), { target: { value: "Keep it short." } });
    fireEvent.click(screen.getByRole("button", { name: words.addNote }));
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    await waitFor(() => expect(document.querySelector("#rs-notes ~ ol, section[aria-labelledby=rs-notes] ol")?.textContent).toContain("Keep it short."));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${words.remove(1)}`) }));
    fireEvent.click(screen.getByRole("button", { name: words.removeYes }));
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(await screen.findByText(words.usingDefaults)).toBeTruthy();
    expect(api.calls.some((c) => c.includes("remove_example:"))).toBe(true);
  });

  it("keeps what was typed when the change fails, and says nothing was changed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.recapStyleAction = async () => ({ state: "failed", error: "not_taken", action: "add_example", createdAt: 1, updatedAt: 2, serverNow: 3 });
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.exampleLabel), { target: { value: "Keep these words." } });
    fireEvent.click(screen.getByRole("button", { name: words.addExample }));
    for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(await screen.findByText(words.failedReasons["not_taken"] as string)).toBeTruthy();
    expect((screen.getByLabelText(words.exampleLabel) as HTMLTextAreaElement).value).toBe("Keep these words.");
  });

  it("asks before deleting, and sends nothing on Keep mine", async () => {
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.click(screen.getByRole("button", { name: words.useDefaults }));
    expect(screen.getByText(words.deleteAsk)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: words.keep }));
    expect(screen.queryByText(words.deleteAsk)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("changeRecapStyle:"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: words.useDefaults }));
    fireEvent.click(screen.getByRole("button", { name: words.deleteYes }));
    await waitFor(() => expect(api.calls.some((c) => c.includes("changeRecapStyle:100.cmp1:use_defaults"))).toBe(true));
  });

  it("is not found, not blank, for a broken address", () => {
    render(<RecapStyleView api={mockApi("table")} path="/account/recap-style/%E0%A4%A" />);
    expect(screen.getByText(words.notFound)).toBeTruthy();
  });

  it("says a recap is over the word limit as it is typed, and will not send it", async () => {
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.exampleLabel), { target: { value: Array(STYLE_EXAMPLE_WORDS + 1).fill("word").join(" ") } });
    expect((await screen.findByRole("alert")).textContent).toBe(words.tooManyWords(STYLE_EXAMPLE_WORDS + 1));
    expect((screen.getByRole("button", { name: words.addExample }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.calls.some((c) => c.startsWith("changeRecapStyle:"))).toBe(false);
  });

  it("says why when the door refuses", async () => {
    const api = mockApi("table");
    api.changeRecapStyle = async () => {
      throw new ApiError("too-soon");
    };
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.noteLabel), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: words.addNote }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.problems["too-soon"]);
  });

  it("offers no add form with three examples, and says so", async () => {
    const api = mockApi("table");
    api.recapStyle = async () => ({ defaults: false, notes: [], examples: [1, 2, 3].map((n) => ({ id: String(n), text: `Example ${n}` })) });
    render(<RecapStyleView api={api} path={PATH} />);
    expect(await screen.findByText(words.exampleFull)).toBeTruthy();
    expect(screen.queryByLabelText(words.exampleLabel)).toBeNull();
  });
});

describe("the wait and the refusals (Supervisor's send-back on #3059)", () => {
  it("does not hold the wait panel at 'waiting for you': a change that sits untouched reads as stalled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.recapStyleAction = async () => ({ state: "waiting", error: "", action: "add_note", createdAt: 100, updatedAt: 100, serverNow: 100 + 400 });
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.noteLabel), { target: { value: "Keep it short." } });
    fireEvent.click(screen.getByRole("button", { name: words.addNote }));
    for (let i = 0; i < 2; i++) await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(await screen.findByText(/Nothing new for a while/)).toBeTruthy();
    expect(document.querySelector(".work-progress")?.getAttribute("data-state")).toBe("stalled");
  });

  it("shows a running clock while the change is being made", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.recapStyleAction = async () => ({ state: "working", error: "", action: "add_note", createdAt: 100, updatedAt: 150, serverNow: 160 });
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.noteLabel), { target: { value: "Keep it short." } });
    fireEvent.click(screen.getByRole("button", { name: words.addNote }));
    for (let i = 0; i < 2; i++) await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(document.querySelector(".work-progress")?.getAttribute("data-state")).toBe("running");
    expect(document.body.textContent).toMatch(/Running for/);
  });

  it("tells a DM who asks for a fourth example that they have three", async () => {
    const api = mockApi("table");
    api.changeRecapStyle = async () => {
      throw new ApiError("full");
    };
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.exampleLabel), { target: { value: "One more." } });
    fireEvent.click(screen.getByRole("button", { name: words.addExample }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.problems["full"]);
  });

  it("turns the server's 409 full into that same refusal", async () => {
    const api = httpApi("/x", async () => new Response(JSON.stringify({ error: "full" }), { status: 409, headers: { "content-type": "application/json" } }));
    await expect(api.changeRecapStyle?.("1.a", "add_example", "words")).rejects.toMatchObject({ kind: "full" });
  });

  it("does not say only the game master can see the examples, because co-DMs and the backup can", () => {
    expect(words.lead).not.toMatch(/Only you/);
    expect(words.lead).toContain("backup");
    expect(words.lead).toContain("game masters");
  });
});
