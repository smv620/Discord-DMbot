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
    expect(screen.getByText(/give this file again to use it/)).toBeTruthy();
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
    const adv = await api.adventureApi!();
    adv.adventure = async () => {
      tries += 1;
      if (tries === 1) throw new ApiError("network");
      return (await mockApi("table").adventureApi!()).adventure("100.cmp1");
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
    fireEvent.click(screen.getAllByRole("button", { name: /^Remove / })[0] as HTMLElement);
    expect(screen.getByText(w.removeAsk("The Glass Orchard.pdf"))).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("adventureRemove"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: w.removeNo }));
    expect(screen.queryByText(w.removeAsk("The Glass Orchard.pdf"))).toBeNull();
    expect(api.calls.some((c) => c.startsWith("adventureRemove"))).toBe(false);
  });

  it("removes a file after the yes, shows it working, and the list no longer has it", async () => {
    const api = show();
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    fireEvent.click(screen.getAllByRole("button", { name: /^Remove / })[0] as HTMLElement);
    const yes = screen.getByRole("button", { name: w.removeYes });
    fireEvent.click(yes);
    fireEvent.click(yes); // (a double press asks once)
    expect(await screen.findByText(w.removing, {}, slow)).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /^Remove / })).toHaveLength(0); // (no other file can be started meanwhile)
    expect(await screen.findByText(w.removed, {}, slow)).toBeTruthy();
    await vi.waitFor(() => expect(screen.queryByText("The Glass Orchard.pdf")).toBeNull(), { timeout: 9000 });
    expect(api.calls.filter((c) => c.startsWith("adventureRemove:"))).toEqual(["adventureRemove:100.cmp1:d1"]);
  }, 30_000);

  it("clears what was read when a file is removed while it is open, and reads it again", async () => {
    const api = show();
    fireEvent.click(await screen.findByRole("button", { name: w.see }, slow));
    await screen.findByText(w.of(50, 120), {}, slow);
    fireEvent.click(screen.getAllByRole("button", { name: /^Remove / })[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.removeYes }));
    await screen.findByText(w.removed, {}, slow);
    await vi.waitFor(() => expect(api.calls.filter((c) => c.startsWith("adventureRead")).length).toBeGreaterThanOrEqual(2), { timeout: 9000 });
  }, 30_000);

  it("stops watching a removal when the page goes away", async () => {
    const api = show();
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    fireEvent.click(screen.getAllByRole("button", { name: /^Remove / })[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.removeYes }));
    await screen.findByText(w.removing, {}, slow);
    cleanup();
    const seen = api.calls.filter((c) => c.startsWith("adventureRemoval")).length;
    await new Promise((ok) => setTimeout(ok, 2500));
    expect(api.calls.filter((c) => c.startsWith("adventureRemoval")).length).toBeLessThanOrEqual(seen + 1);
  }, 30_000);

  it("offers no Remove when the API cannot remove", async () => {
    const api = mockApi("table");
    delete ((await api.adventureApi!()) as { adventureRemoval?: unknown }).adventureRemoval;
    render(<Adventure api={api} refId="100.cmp1" />);
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    expect(screen.queryAllByRole("button", { name: /^Remove / })).toHaveLength(0);
  });

  it("says plainly when the removal failed, and leaves the file in the list", async () => {
    show();
    await screen.findByText("Second Book.docx", {}, slow);
    fireEvent.click(screen.getAllByRole("button", { name: /^Remove / })[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.removeYes }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.removeFailed);
    expect(screen.getByText("Second Book.docx")).toBeTruthy();
  }, 30_000);

  it("drops a late answer after Hide, so See again starts clean, and a failed Show more keeps what is on screen", async () => {
    const api = mockApi("table");
    const adv = await api.adventureApi!();
    const real = adv.adventureRead.bind(adv);
    let n = 0;
    let release: () => void = () => {};
    adv.adventureRead = async (ref, offset) => {
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

describe("giving a file (#3694 slice D)", () => {
  const give = (name: string, size = 100) => new File([new Uint8Array(size)], name);
  const pick = async (file: File) => {
    fireEvent.click(await screen.findByRole("button", { name: w.give }, slow));
    fireEvent.change(screen.getByLabelText(w.giveFile), { target: { files: [file] } });
  };

  it("turns away a file of the wrong kind or too big before anything is sent", async () => {
    const api = mockApi("table");
    render(<Adventure api={api} refId="100.cmp1" />);
    await pick(give("map.png"));
    expect(screen.getByRole("alert").textContent).toBe(w.giveWrong);
    fireEvent.change(screen.getByLabelText(w.giveFile), { target: { files: [give("book.docx", 11 * 1024 * 1024)] } });
    expect(screen.getByRole("alert").textContent).toBe(w.giveBig(10));
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    expect(api.calls.some((c) => c.startsWith("adventureGive:"))).toBe(false);
  });

  it("turns away an empty file and one with a very long name, and lets a 20 MB PDF through", async () => {
    render(<Adventure api={mockApi("table")} refId="100.cmp1" />);
    await pick(give("empty.txt", 0));
    expect(screen.getByRole("alert").textContent).toBe(w.giveEmpty);
    fireEvent.change(screen.getByLabelText(w.giveFile), { target: { files: [give(`${"x".repeat(201)}.txt`)] } });
    expect(screen.getByRole("alert").textContent).toBe(w.giveName);
    fireEvent.change(screen.getByLabelText(w.giveFile), { target: { files: [give("big.pdf", 20 * 1024 * 1024)] } });
    expect(screen.queryByRole("alert")).toBeNull(); // (a PDF may be up to 50 MB)
  });

  it("asks for a file first", async () => {
    render(<Adventure api={mockApi("table")} refId="100.cmp1" />);
    fireEvent.click(await screen.findByRole("button", { name: w.give }, slow));
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    expect(screen.getByRole("alert").textContent).toBe(w.giveNone);
  });

  it("gives a file, shows the wait and then that it was kept, and the list has it", async () => {
    const api = mockApi("table");
    render(<Adventure api={api} refId="100.cmp1" />);
    await pick(give("The Salt Road.pdf"));
    const send = screen.getByRole("button", { name: w.giveSend });
    fireEvent.click(send);
    fireEvent.click(send); // (a double press sends once)
    expect(await screen.findByText(w.stagesGive.waiting as string, {}, slow)).toBeTruthy();
    expect(await screen.findByText(w.given("The Salt Road.pdf"), {}, slow)).toBeTruthy();
    expect(await screen.findByText("The Salt Road.pdf", {}, slow)).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("adventureGive:"))).toEqual(["adventureGive:100.cmp1:The Salt Road.pdf:100"]);
  }, 30_000);

  it("says what a refused or failed give means, in plain words", async () => {
    render(<Adventure api={mockApi("table")} refId="100.cmp1" />);
    await pick(give("badfile.pdf"));
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.giveRefused["bad-file"]);
    cleanup();
    render(<Adventure api={mockApi("table")} refId="100.cmp1" />);
    await pick(give("dupfile.txt"));
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.giveFailed.already);
  }, 30_000);

  it("says no text was found, and stops watching when the page goes away", async () => {
    const api = mockApi("table");
    render(<Adventure api={api} refId="100.cmp1" />);
    await pick(give("emptyfile.pdf"));
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.giveFailed.no_text);
    cleanup();
    const second = mockApi("table");
    render(<Adventure api={second} refId="100.cmp1" />);
    await pick(give("The Last Road.pdf"));
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    await screen.findByText(w.stagesGive.waiting as string, {}, slow);
    cleanup();
    const seen = second.calls.filter((c) => c.startsWith("adventureGiving")).length;
    await new Promise((ok) => setTimeout(ok, 2500));
    expect(second.calls.filter((c) => c.startsWith("adventureGiving")).length).toBeLessThanOrEqual(seen + 1);
  }, 30_000);

  it("says a refused give in plain words, and is not stuck when the campaign changes mid-give", async () => {
    const api = mockApi("table");
    const { rerender } = render(<Adventure api={api} refId="100.cmp1" />);
    await pick(give("busyfile.pdf"));
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.giveRefused["slow-down"]);
    fireEvent.change(screen.getByLabelText(w.giveFile), { target: { files: [give("book.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: w.giveSend }));
    await screen.findByText(w.stagesGive.waiting as string, {}, slow);
    rerender(<Adventure api={api} refId="200.cmp2" />);
    expect(await screen.findByRole("button", { name: w.give }, slow)).toBeTruthy(); // (a fresh panel for the new campaign)
    expect(screen.queryByText(w.stagesGive.waiting as string)).toBeNull();
  }, 30_000);

  it("offers no Give a file when the API cannot take one", async () => {
    const api = mockApi("table");
    delete ((await api.adventureApi!()) as { adventureGiving?: unknown }).adventureGiving;
    render(<Adventure api={api} refId="100.cmp1" />);
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    expect(screen.queryByRole("button", { name: w.give })).toBeNull();
  });
});

describe("reading a file now (#3694 slice E)", () => {
  const open = async () => {
    const api = mockApi("table");
    render(<Adventure api={api} refId="100.cmp1" />);
    await screen.findByText("Second Book.docx", {}, slow);
    return api;
  };
  const readButtons = () => screen.queryAllByRole("button", { name: /^Read it now / });

  it("offers Read it now only on a file that has a cost sentence, and shows the cost before anything is sent", async () => {
    const api = await open();
    expect(readButtons()).toHaveLength(1); // (the finished and the restored file have none)
    fireEvent.click(readButtons()[0] as HTMLElement);
    expect(screen.getByText(/Reading this file costs about 12 AI credits/)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("adventureStartRead"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: w.readNo }));
    expect(screen.queryByText(/Reading this file costs/)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("adventureStartRead"))).toBe(false);
  });

  it("starts the read after the yes, and the page then shows how far it is by itself until it is read", async () => {
    const api = await open();
    fireEvent.click(readButtons()[0] as HTMLElement);
    const yes = screen.getByRole("button", { name: w.readYes });
    fireEvent.click(yes);
    fireEvent.click(yes); // (a double press sends once)
    expect(await screen.findByText(w.started, {}, slow)).toBeTruthy();
    expect(await screen.findByText(w.readingStage, {}, slow)).toBeTruthy(); // (stage, how far, how long: the shared wait)
    await vi.waitFor(() => expect(screen.queryByText(w.readingStage)).toBeNull(), { timeout: 20_000 });
    expect(screen.getByText(/read: 3 chapters, 2 places, 9 people/)).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("adventureStartRead"))).toEqual(["adventureStartRead:100.cmp1:d2"]);
  }, 40_000);

  it("stops re-reading the list once nothing is reading", async () => {
    const api = await open();
    fireEvent.click(readButtons()[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.readYes }));
    await screen.findByText(w.started, {}, slow);
    await vi.waitFor(() => expect(screen.queryByText(w.readingStage)).toBeNull(), { timeout: 20_000 });
    await vi.waitFor(() => expect(screen.getByText(/read: 3 chapters/)).toBeTruthy(), { timeout: 25_000 });
    const seen = api.calls.filter((c) => c === "adventure:100.cmp1").length;
    await new Promise((ok) => setTimeout(ok, 3500));
    // (the 30-second just-started window may still re-read once or twice; it must not run on without end)
    expect(api.calls.filter((c) => c === "adventure:100.cmp1").length).toBeLessThanOrEqual(seen + 2);
  }, 60_000);

  it("offers no Read it now when the API cannot start a read", async () => {
    const api = mockApi("table");
    delete ((await api.adventureApi!()) as { adventureStarting?: unknown }).adventureStarting;
    render(<Adventure api={api} refId="100.cmp1" />);
    await screen.findByText("Second Book.docx", {}, slow);
    expect(readButtons()).toHaveLength(0);
  });
});

describe("Guide me: where is the table (#3694 slice F)", () => {
  const open = async (ref = "100.cmp1") => {
    const api = mockApi("table");
    render(<Adventure api={api} refId={ref} />);
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    return api;
  };

  it("shows Discord's message as it is, and nothing before the DM asks", async () => {
    const api = await open();
    expect(api.calls.some((c) => c.startsWith("adventureGuide:"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: w.guide }));
    expect(await screen.findByText(/The table seems to be in Made-up chapter two\./, {}, slow)).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("adventureGuide:"))).toEqual(["adventureGuide:100.cmp1:d1:read:-"]); // (the first file that has been read)
  }, 30_000);

  it("picks another chapter, offers the way back to DMbot's guess and to the chapter it was on, and hides again", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.guide }));
    await screen.findByText(/seems to be in Made-up chapter two/, {}, slow);
    fireEvent.change(screen.getByLabelText(w.guideAnother), { target: { value: "c1" } });
    fireEvent.click(screen.getByRole("button", { name: w.guideUse }));
    expect(await screen.findByText(/The table is in Made-up chapter one \(you said so\)/, {}, slow)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.guideBackGuess })).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.guideBackTo("Made-up chapter one") })).toBeNull(); // (it was DMbot's guess before, so only the guess to go back to)
    fireEvent.change(screen.getByLabelText(w.guideAnother), { target: { value: "c3" } });
    fireEvent.click(screen.getByRole("button", { name: w.guideUse }));
    expect(await screen.findByText(/The table is in Made-up chapter three/, {}, slow)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.guideBackTo("Made-up chapter one") })).toBeTruthy(); // (the chapter it was on)
    fireEvent.click(screen.getByRole("button", { name: w.guideBackTo("Made-up chapter one") }));
    expect(await screen.findByText(/The table is in Made-up chapter one/, {}, slow)).toBeTruthy(); // (back to the chapter it was on)
    fireEvent.click(screen.getByRole("button", { name: w.guideBackGuess }));
    expect(await screen.findByText(/seems to be in Made-up chapter two/, {}, slow)).toBeTruthy();
    expect(api.calls.filter((c) => c.startsWith("adventureGuide:")).map((c) => c.split(":").slice(3).join(":"))).toEqual(["read:-", "pick:c1", "pick:c3", "pick:c1", "clear:-"]);
    fireEvent.click(screen.getByRole("button", { name: w.guideHide }));
    expect(screen.queryByText(/seems to be in/)).toBeNull();
  }, 60_000);

  it("sends one ask for a double press, and never puts the book's words in a call", async () => {
    const api = await open();
    const ask = screen.getByRole("button", { name: w.guide });
    fireEvent.click(ask);
    fireEvent.click(ask);
    await screen.findByText(/seems to be in Made-up chapter two/, {}, slow);
    expect(api.calls.filter((c) => c.startsWith("adventureGuide:"))).toHaveLength(1);
    expect(api.calls.join("\n")).not.toContain("Made-up place");
  }, 30_000);

  it("says plainly when Guide me is not switched on, and offers no button while Shared story only watches", async () => {
    await open("100.cmp1.not-guide");
    fireEvent.click(screen.getByRole("button", { name: w.guide }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.guideRefused["not-on"]);
    expect(screen.getByRole("button", { name: w.guide })).toBeTruthy(); // (it can be asked again)
    cleanup();
    await open("100.cmp1.adventure-watch");
    expect(screen.queryByRole("button", { name: w.guide })).toBeNull();
  });
});

describe("the real client's adventure calls (their names are not in the first download)", () => {
  it("loads on first use, sends the file as it is with its name in the address, and reads the adventure's own refusals", async () => {
    const { httpApi } = await import("../src/account/api");
    const seen: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      seen.push({ url, method: String(init.method), body: init.body });
      if (url.includes("/give")) return new Response(JSON.stringify({ detail: "not_on" }), { status: 409, headers: { "content-type": "application/json" } });
      return new Response(JSON.stringify({ detail: "not_readable" }), { status: 409, headers: { "content-type": "application/json" } });
    });
    const api = httpApi("/api");
    expect((api as { adventure?: unknown }).adventure).toBeUndefined(); // (no adventure names on the main API)
    const adv = await api.adventureApi!();
    await expect(adv.adventureGive("100.cmp1", new Blob(["x"]), "Book one.pdf")).rejects.toMatchObject({ kind: "not-on" });
    expect(seen[0]!.url).toBe("/api/me/campaigns/100.cmp1/adventure/give?name=Book%20one.pdf");
    await expect(adv.adventureStartRead("100.cmp1", "d1")).rejects.toMatchObject({ kind: "not-readable" });
    expect(seen[1]!.url).toBe("/api/me/campaigns/100.cmp1/adventure/d1/read");
    expect(await api.adventureApi!()).toBe(adv); // (loaded once)
    vi.unstubAllGlobals();
  });
});

describe("Match names (#3694 slice G1)", () => {
  const open = async (ref = "100.cmp1") => {
    const api = mockApi("table");
    render(<Adventure api={api} refId={ref} />);
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    return api;
  };
  const pairLegend = (plan: string) => screen.getByText(new RegExp(`In your adventure: ${plan}`));
  const callsOf = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("adventureMatch:"));

  it("looks only when asked, then lists each pair with Same, Not the same and Skip, in Discord's words", async () => {
    const api = await open();
    expect(callsOf(api)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: w.match }));
    expect(await screen.findByText(w.matchHead, {}, slow)).toBeTruthy();
    expect(await screen.findByText(/In your adventure: Captain Varn \(character\)\. DMbot remembers: Varn/, {}, slow)).toBeTruthy();
    expect(pairLegend("The Salt Gate")).toBeTruthy();
    expect(screen.getAllByRole("radio", { name: w.matchSkip }).every((r) => (r as HTMLInputElement).checked)).toBe(true); // (nothing is chosen for the DM)
    expect(callsOf(api)).toEqual(["adventureMatch:100.cmp1:suggest"]);
  }, 30_000);

  it("saves only the answered pairs, shows how many, and Undo takes the save back", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.match }));
    await screen.findByText(/Captain Varn/, {}, slow);
    fireEvent.click(screen.getAllByRole("radio", { name: w.matchSame })[0] as HTMLElement);
    fireEvent.click(screen.getAllByRole("radio", { name: w.matchNot })[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.matchSave }));
    expect(await screen.findByText(w.matchSaved(2), {}, slow)).toBeTruthy();
    expect(callsOf(api)[1]).toBe("adventureMatch:100.cmp1:decide:b1>e1=same,b2>e2=not_same");
    expect(screen.queryByText(/Captain Varn/)).toBeNull(); // (answered pairs leave the list)
    fireEvent.click(screen.getByRole("button", { name: w.matchUndo }));
    expect(await screen.findByText(w.matchUndoneLook, {}, slow)).toBeTruthy();
    expect(callsOf(api)[2]).toBe("adventureMatch:100.cmp1:undo:match-2");
  }, 60_000);

  it("asks for at least one answer, and sends nothing without one", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: w.match }));
    await screen.findByText(/Captain Varn/, {}, slow);
    fireEvent.click(screen.getByRole("button", { name: w.matchSave }));
    expect(screen.getByRole("alert").textContent).toBe(w.matchNone);
    expect(callsOf(api)).toHaveLength(1);
  }, 30_000);

  it("says plainly when nothing matches, and when a save stopped part way with the reason as a code worded here", async () => {
    await open("100.cmp1.match-none");
    fireEvent.click(screen.getByRole("button", { name: w.match }));
    expect(await screen.findByText(w.matchNothing, {}, slow)).toBeTruthy();
    cleanup();
    const api = await open("100.cmp1.match-taken");
    fireEvent.click(screen.getByRole("button", { name: w.match }));
    await screen.findByText(/Captain Varn/, {}, slow);
    fireEvent.click(screen.getAllByRole("radio", { name: w.matchSame })[0] as HTMLElement);
    fireEvent.click(screen.getAllByRole("radio", { name: w.matchSame })[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.matchSave }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.matchRefusals.taken_book);
    expect(await screen.findByText(w.matchSaved(1), {}, slow)).toBeTruthy(); // (the pick before it was written)
    expect(screen.queryByText(/Captain Varn/)).toBeNull(); // (the written pair leaves the list)
    expect(pairLegend("The Salt Gate")).toBeTruthy(); // (the pair that stopped the save stays, to be looked at again)
    expect(callsOf(api)).toHaveLength(2);
  }, 60_000);

  it("says a refused look in plain words, and Close brings the button back", async () => {
    await open("100.cmp1.match-busy");
    fireEvent.click(screen.getByRole("button", { name: w.match }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.matchRefused["slow-down"]);
    fireEvent.click(screen.getByRole("button", { name: w.matchHide }));
    expect(screen.getByRole("button", { name: w.match })).toBeTruthy();
  }, 30_000);
});

describe("Match names: the undo's own note (#3694 slice G1)", () => {
  it("says it was too late when core says so, and a second look is offered", async () => {
    const api = mockApi("table");
    render(<Adventure api={api} refId="100.cmp1.match-late" />);
    await screen.findByText("The Glass Orchard.pdf", {}, slow);
    fireEvent.click(screen.getByRole("button", { name: w.match }));
    await screen.findByText(/Captain Varn/, {}, slow);
    fireEvent.click(screen.getAllByRole("radio", { name: w.matchSame })[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: w.matchSave }));
    await screen.findByText(w.matchSaved(1), {}, slow);
    fireEvent.click(screen.getByRole("button", { name: w.matchUndo }));
    expect(await screen.findByText(w.matchUndoNotes.too_late as string, {}, slow)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.matchAgain })).toBeTruthy();
  }, 60_000);
});

describe("one name's link to the adventure (#3694 slice G2)", () => {
  const live2 = async (ref = "100.cmp1") => {
    const api = mockApi("table");
    const { default: AdventureLink } = await import("../src/account/AdventureLink");
    render(<AdventureLink api={api} refId={ref} entity={{ id: "n1", name: "Mira" }} />);
    return api;
  };
  const callsOf = (api: ReturnType<typeof mockApi>) => api.calls.filter((c) => c.startsWith("adventureMatch:"));

  it("asks nothing until the DM opens it, then lists the book's entries twenty-five at a time, in Discord's words", async () => {
    const api = await live2();
    expect(callsOf(api)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: w.linkOpen }));
    expect(await screen.findByText(w.linkWhich("Mira"), {}, slow)).toBeTruthy();
    expect(await screen.findByText(/In your adventure: Entry 1 \(Chapter 1\)/, {}, slow)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^Link to this one: / })).toHaveLength(25);
    fireEvent.click(screen.getByRole("button", { name: w.linkMore }));
    expect(await screen.findByText(/Entry 30/, {}, slow)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^Link to this one: / })).toHaveLength(30);
    expect(callsOf(api)).toEqual(["adventureMatch:100.cmp1:options", "adventureMatch:100.cmp1:options"]);
  }, 60_000);

  it("links to one entry, says so, shows it as linked with Take off, and Undo takes it back", async () => {
    const api = await live2();
    fireEvent.click(screen.getByRole("button", { name: w.linkOpen }));
    await screen.findByText(/Entry 2 /, {}, slow);
    fireEvent.click(screen.getByRole("button", { name: "Link to this one: Entry 2" }));
    expect(await screen.findByText(w.linkLinked("Entry 2"), {}, slow)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Take off Entry 2" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Link to this one: Entry 2" })).toBeNull(); // (it is linked now)
    fireEvent.click(screen.getByRole("button", { name: w.matchUndo }));
    expect(await screen.findByText(w.matchUndone, {}, slow)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Take off Entry 2" })).toBeNull();
    expect(callsOf(api).some((c) => c.includes(":pick"))).toBe(true);
  }, 90_000);

  it("takes a link off, and says plainly when the book has nothing or the store refuses", async () => {
    await live2("100.cmp1.link-none");
    fireEvent.click(screen.getByRole("button", { name: w.linkOpen }));
    expect(await screen.findByText(w.linkNothing, {}, slow)).toBeTruthy();
    cleanup();
    await live2("100.cmp1.link-taken");
    fireEvent.click(screen.getByRole("button", { name: w.linkOpen }));
    await screen.findByText(/Entry 1 /, {}, slow);
    fireEvent.click(screen.getByRole("button", { name: "Link to this one: Entry 1" }));
    expect((await screen.findByRole("alert", {}, slow)).textContent).toBe(w.matchRefusals.taken_book);
  }, 60_000);
});

describe("the link on a name's details (#3694 slice G2)", () => {
  const onPage = async (path: string) => {
    const { default: Web } = await import("../src/account/Web");
    const api = mockApi("table");
    render(<Web api={api} path={path} />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/, {}, { timeout: 8000 });
    fireEvent.click((await screen.findAllByText(/Mira Voss/, { selector: "text" }))[0] as HTMLElement);
    return api;
  };

  it("is offered to the DM on a name's details, only while its switch is on", async () => {
    await onPage("/account/web/100.cmp1");
    expect(await screen.findByRole("button", { name: w.linkOpen }, { timeout: 8000 })).toBeTruthy();
    cleanup();
    live.on = false;
    await onPage("/account/web/100.cmp1");
    await new Promise((ok) => setTimeout(ok, 500));
    expect(screen.queryByRole("button", { name: w.linkOpen })).toBeNull();
  }, 40_000);

  it("is not offered to a player", async () => {
    await onPage("/account/web/100.cmp1.web-player");
    await new Promise((ok) => setTimeout(ok, 500));
    expect(screen.queryByRole("button", { name: w.linkOpen })).toBeNull();
  }, 30_000);
});
