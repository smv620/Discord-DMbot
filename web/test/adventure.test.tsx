/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Your adventure" on the Upload lore page (#3694 slices A and B, behind ADVENTURE_LIVE; core #5300, #5305): the files and how far each
// read is in Discord's words, what was read fifty at a time, nothing at all for anyone but a DM or while the switch is off, and a failed
// look that says so and offers another try. Everything in it is made up: no real book is in a test.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Adventure, { readLine } from "../src/account/Adventure";
import { ApiError, type AdventureDocument } from "../src/account/api";
import Lore from "../src/account/Lore";
import { mockApi } from "../src/account/mock";
import { adventureWords as w } from "../src/content/adventure-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/adventure-switch", () => ({
  get ADVENTURE_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const slow = { timeout: 9000 };
const doc = (over: Partial<AdventureDocument> = {}): AdventureDocument => ({ id: "d", name: "x", characters: 2000, restored: false, addedAt: 1, read: null, found: { chapters: 0, places: 0, people: 0, links: 0 }, ...over });

describe("how far a read is, in Discord's words", () => {
  it("says not read yet, and what a finished read found, with the parts that could not be read", () => {
    expect(readLine(doc())).toBe("not read yet");
    const done = doc({ read: { status: "done", nextChunk: 4, totalChunks: 4, stopReason: "", skippedParts: 1, updatedAt: 1 }, found: { chapters: 1, places: 2, people: 30, links: 0 } });
    expect(readLine(done)).toBe(`read: 1 chapter, 2 places, 30 people, 1 part couldn't be read`);
    expect(readLine(doc({ read: { status: "done", nextChunk: 4, totalChunks: 4, stopReason: "", skippedParts: 0, updatedAt: 1 } }))).toBe(w.nothingFound);
  });

  it("says the percent while reading, and why a stopped read stopped and what to do", () => {
    expect(readLine(doc({ read: { status: "reading", nextChunk: 5, totalChunks: 20, stopReason: "", skippedParts: 0, updatedAt: 1 } }))).toBe("reading… 25%");
    const stopped = readLine(doc({ read: { status: "stopped", nextChunk: 5, totalChunks: 20, stopReason: "month_cap", skippedParts: 0, updatedAt: 1 } }));
    expect(stopped).toBe(`stopped at 25%. ${w.why.month_cap}`);
    expect(readLine(doc({ read: { status: "stopped", nextChunk: 1, totalChunks: 2, stopReason: "never-heard-of", skippedParts: 0, updatedAt: 1 } }))).toContain(w.carryOn);
  });
});

describe("the panel", () => {
  const show = (ref = "100.cmp1") => {
    const api = mockApi("table");
    render(<Adventure api={api} refId={ref} />);
    return api;
  };

  it("lists each file with its size and where its read stands, and says what is held", async () => {
    show();
    expect(await screen.findByText("The Glass Orchard.pdf", {}, slow)).toBeTruthy();
    expect(screen.getByText(/about 60 pages, read: 12 chapters, 1 place, 30 people, 4 "this needs that" notes, 2 parts couldn't be read/)).toBeTruthy();
    expect(screen.getByText(/stopped at 25%\. DMbot has used up its reading for this month/)).toBeTruthy();
    expect(screen.getByText(/give this file again in Discord to use it/)).toBeTruthy();
    expect(screen.getByText(w.using(163000, 2000000))).toBeTruthy();
  });

  it("says when there is nothing yet, and when Shared story is off", async () => {
    show("100.no-adventure");
    expect(await screen.findByText(w.empty, {}, slow)).toBeTruthy();
    cleanup();
    show("100.adventure-off");
    expect(await screen.findByText(w.notOn, {}, slow)).toBeTruthy();
  });

  it("shows nothing at all to anyone but a DM", async () => {
    const api = mockApi("table");
    render(<Adventure api={api} refId="100.cmp1.web-player" />);
    await new Promise((ok) => setTimeout(ok, 300));
    expect(document.body.textContent).toBe("");
  });

  it("says a failed look failed and offers another try", async () => {
    const api = mockApi("table");
    let tries = 0;
    api.adventure = async () => {
      tries += 1;
      if (tries === 1) throw new ApiError("network");
      return mockApi("table").adventure!("100.cmp1");
    };
    render(<Adventure api={api} refId="100.cmp1" />);
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.down);
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    expect(await screen.findByText("The Glass Orchard.pdf", {}, slow)).toBeTruthy();
  });

  it("shows what was read fifty at a time, and Show more brings the rest", async () => {
    const api = show();
    fireEvent.click(await screen.findByRole("button", { name: w.see }, slow));
    expect(await screen.findByText(w.of(50, 120), {}, slow)).toBeTruthy();
    expect(screen.getByText("Made-up chapter 4")).toBeTruthy();
    expect(screen.getByText("Gate 3 → Key 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.more(50) }));
    expect(await screen.findByText(w.of(100, 120), {}, slow)).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("adventureRead"))).toEqual(["adventureRead:100.cmp1:0", "adventureRead:100.cmp1:50"]);
    fireEvent.click(screen.getByRole("button", { name: w.hide }));
    expect(screen.queryByText("Made-up chapter 4")).toBeNull();
  });

  it("asks once before removing a file, and Keep it changes nothing", async () => {
    const api = show();
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    fireEvent.click(screen.getAllByRole("button", { name: w.remove })[0] as HTMLElement);
    expect(screen.getByText(w.removeAsk("The Glass Orchard.pdf"))).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("adventureRemove"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: w.removeNo }));
    expect(screen.queryByText(w.removeAsk("The Glass Orchard.pdf"))).toBeNull();
    expect(api.calls.some((c) => c.startsWith("adventureRemove"))).toBe(false);
  });

  it("removes a file after the yes, shows it working, and the list no longer has it", async () => {
    const api = show();
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    fireEvent.click(screen.getAllByRole("button", { name: w.remove })[0] as HTMLElement);
    const yes = screen.getByRole("button", { name: w.removeYes });
    fireEvent.click(yes);
    fireEvent.click(yes); // (a double press asks once)
    expect(await screen.findByText(w.removing, {}, slow)).toBeTruthy();
    await vi.waitFor(() => expect(screen.queryByText("The Glass Orchard.pdf")).toBeNull(), { timeout: 9000 });
    expect(api.calls.filter((c) => c.startsWith("adventureRemove:"))).toEqual(["adventureRemove:100.cmp1:d1"]);
  }, 30_000);

  it("says plainly when the removal failed, and leaves the file in the list", async () => {
    show();
    await screen.findByText("Second Book.docx", {}, slow);
    fireEvent.click(screen.getAllByRole("button", { name: w.remove })[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.removeYes }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.removeFailed);
    expect(screen.getByText("Second Book.docx")).toBeTruthy();
  }, 30_000);

  it("drops a late answer after Hide, so See again starts clean, and a failed Show more keeps what is on screen", async () => {
    const api = mockApi("table");
    const real = api.adventureRead!.bind(api);
    let n = 0;
    let release: () => void = () => {};
    api.adventureRead = async (ref, offset) => {
      n += 1;
      if (n === 2) await new Promise<void>((ok) => (release = ok)); // (the second ask is slow)
      if (n === 4) throw new ApiError("network");
      return real(ref, offset);
    };
    render(<Adventure api={api} refId="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: w.see }, slow));
    await screen.findByText(w.of(50, 120), {}, slow);
    fireEvent.click(screen.getByRole("button", { name: w.more(50) })); // (slow)
    fireEvent.click(screen.getByRole("button", { name: w.hide }));
    release();
    fireEvent.click(await screen.findByRole("button", { name: w.see }));
    expect(await screen.findByText(w.of(50, 120), {}, slow)).toBeTruthy(); // (not 100: the late page was dropped)
    fireEvent.click(screen.getByRole("button", { name: w.more(50) }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.readDown);
    expect(screen.getByText(w.of(50, 120))).toBeTruthy(); // (what was shown stays)
  });

  it("sends one ask for a double press of Show more", async () => {
    const api = show();
    fireEvent.click(await screen.findByRole("button", { name: w.see }, slow));
    await screen.findByText(w.of(50, 120), {}, slow);
    const more = screen.getByRole("button", { name: w.more(50) });
    fireEvent.click(more);
    fireEvent.click(more);
    await screen.findByText(w.of(100, 120), {}, slow);
    expect(api.calls.filter((c) => c.startsWith("adventureRead"))).toEqual(["adventureRead:100.cmp1:0", "adventureRead:100.cmp1:50"]);
  });

  it("offers neither the list nor See while Shared story is off, and Refresh reads the files again", async () => {
    const api = show("100.adventure-off");
    expect(await screen.findByText(w.notOn, {}, slow)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.see })).toBeNull();
    cleanup();
    const on = show();
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    fireEvent.click(screen.getByRole("button", { name: w.refresh }));
    await vi.waitFor(() => expect(on.calls.filter((c) => c === "adventure:100.cmp1")).toHaveLength(2));
    void api;
  });

  it("says nothing was read when nothing was", async () => {
    show("100.nothing-read");
    fireEvent.click(await screen.findByRole("button", { name: w.see }, slow));
    expect(await screen.findByText(w.nothingRead, {}, slow)).toBeTruthy();
  });
});

describe("on the Upload lore page", () => {
  it("appears under the form only while its switch is on", async () => {
    render(<Lore api={mockApi("table")} path="/account/lore/100.cmp1" />);
    expect(await screen.findByRole("heading", { name: w.heading }, slow)).toBeTruthy();
    cleanup();
    live.on = false;
    render(<Lore api={mockApi("table")} path="/account/lore/100.cmp1" />);
    await new Promise((ok) => setTimeout(ok, 500));
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });
});
