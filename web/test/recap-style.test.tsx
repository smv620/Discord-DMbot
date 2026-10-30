/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Recap style on the website (#2985): the examples and notes, each change queued and followed to its end.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
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
    fireEvent.click(screen.getByRole("button", { name: words.remove(1) }));
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(await screen.findByText(words.usingDefaults)).toBeTruthy();
    expect(api.calls.some((c) => c.includes("remove_example:"))).toBe(true);
  });

  it("refuses a recap over the word limit before sending", async () => {
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    fireEvent.input(screen.getByLabelText(words.exampleLabel), { target: { value: Array(STYLE_EXAMPLE_WORDS + 1).fill("word").join(" ") } });
    fireEvent.click(screen.getByRole("button", { name: words.addExample }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.tooManyWords);
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
