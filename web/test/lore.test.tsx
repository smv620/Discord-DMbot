/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Upload lore (#3022): the form, what is sent, the right-to-use box, the wait and where to check what was found.
import { cleanup, fireEvent, render, screen, waitFor, act } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { lorePage as words, LORE_TEXT_MAX } from "../src/content/lore-words";
import { ApiError } from "../src/account/api";
import Lore from "../src/account/Lore";
import { mockApi } from "../src/account/mock";

const PATH = "/account/lore/100.cmp1";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function fill(text = "Mira was raised by the Ashen Crown.") {
  fireEvent.input(await screen.findByLabelText(words.pasteLabel), { target: { value: text } });
}

describe("Upload lore", () => {
  it("asks for the right to use the text before anything is sent", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    await fill();
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.rightsNeeded);
    expect(api.calls.some((c) => c.startsWith("loreAdd:"))).toBe(false);
  });

  it("sends the words with the choices, then shows the wait and never shows the words back", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    await fill();
    fireEvent.click(screen.getByLabelText(words.known.spoilers.label, { exact: false }));
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(words.stages.waiting));
    expect(api.calls).toContain("loreAdd:100.cmp1:paste:spoilers:-:rights:35");
    expect(document.body.textContent).not.toContain("Ashen Crown");
  });

  it("sends a chosen character as whose backstory it is", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    const select = (await screen.findByLabelText(words.speakerLabel)) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1));
    fireEvent.change(select, { target: { value: select.options[1]!.value } });
    await fill();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("loreAdd:") && !c.includes(":-:"))).toBe(true));
  });

  it("sends a link, not words, and refuses nothing typed", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    fireEvent.click(screen.getByLabelText(words.sources.link));
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.nothingYet);
    fireEvent.input(screen.getByLabelText(words.linkLabel), { target: { value: "https://example.com/lore" } });
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("loreAdd:100.cmp1:link:"))).toBe(true));
  });

  it("says plainly when a file is too long, and sends none of it", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    fireEvent.click(screen.getByLabelText(words.sources.file));
    const file = new File(["x".repeat(LORE_TEXT_MAX + 1)], "lore.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(words.fileLabel), { target: { files: [file] } });
    expect((await screen.findByRole("alert")).textContent).toBe(words.fileTooLong);
  });

  it("takes a 100 KB text file", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    fireEvent.click(screen.getByLabelText(words.sources.file));
    fireEvent.change(screen.getByLabelText(words.fileLabel), { target: { files: [new File(["a".repeat(100_000)], "lore.txt", { type: "text/plain" })] } });
    expect(await screen.findByText(words.fileChosen("lore.txt"))).toBeTruthy();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    await waitFor(() => expect(api.calls).toContain("loreAdd:100.cmp1:file:smart:-:rights:100000"));
  });

  it("reads a text file in the browser and sends its words", async () => {
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    fireEvent.click(screen.getByLabelText(words.sources.file));
    fireEvent.change(screen.getByLabelText(words.fileLabel), { target: { files: [new File(["Old lore."], "lore.txt", { type: "text/plain" })] } });
    expect((await screen.findByText(words.fileChosen("lore.txt")))).toBeTruthy();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    await waitFor(() => expect(api.calls).toContain("loreAdd:100.cmp1:file:smart:-:rights:9"));
  });

  it("keeps what was typed when an upload fails, and offers Try again", async () => {
    const api = mockApi("table");
    api.loreAdd = async () => ({ id: "0".repeat(32), state: "failed", stage: "reading", error: "not_taken", source: "paste", choice: "smart", namesFound: 0, linksFound: 0, done: 0, total: 0, createdAt: 1, updatedAt: 2, serverNow: 3 });
    render(<Lore api={api} path={PATH} />);
    await fill("Keep these words.");
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    fireEvent.click(await screen.findByRole("button", { name: words.retry }));
    expect(((await screen.findByLabelText(words.pasteLabel)) as HTMLTextAreaElement).value).toBe("Keep these words.");
  });

  it("says plainly when nothing was found", async () => {
    const api = mockApi("table");
    api.loreAdd = async () => ({ id: "0".repeat(32), state: "done", stage: "done", error: "", source: "paste", choice: "smart", namesFound: 0, linksFound: 0, done: 0, total: 0, createdAt: 1, updatedAt: 2, serverNow: 3 });
    render(<Lore api={api} path={PATH} />);
    await fill();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    expect(await screen.findByText(words.foundNothing)).toBeTruthy();
  });

  it("says why when the door refuses", async () => {
    const api = mockApi("table");
    api.loreAdd = async () => {
      throw new ApiError("too-soon");
    };
    render(<Lore api={api} path={PATH} />);
    await fill();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    expect((await screen.findByRole("alert")).textContent).toBe(words.problems["too-soon"]);
  });

  it("follows the upload to done and points at Check the new names and the Story Graph", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<Lore api={api} path={PATH} />);
    await fill();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(await screen.findByText(words.found(4, 3))).toBeTruthy();
    expect((screen.getByText(words.checkNames) as HTMLAnchorElement).getAttribute("href")).toBe("/account/names/100.cmp1");
    expect((screen.getByText(words.seeGraph) as HTMLAnchorElement).getAttribute("href")).toBe("/account/web/100.cmp1");
  });

  it("shows an upload that is already open when a second one is turned away", async () => {
    const api = mockApi("table");
    await api.loreAdd?.("100.cmp1", { source: "paste", text: "a", choice: "smart", speaker: "", rights: true });
    render(<Lore api={api} path={PATH} />);
    expect(await screen.findByText(words.stages["waiting"] as string)).toBeTruthy();
  });
});

describe("the wait panel on Upload lore (Supervisor's send-back on #3059)", () => {
  it("does not hold the panel at 'waiting for you': an upload nobody has picked up for a while reads as stalled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.loreAdd = async () => ({ id: "0".repeat(32), state: "waiting", stage: "waiting", error: "", source: "paste", choice: "smart", namesFound: 0, linksFound: 0, done: 0, total: 0, createdAt: 100, updatedAt: 100, serverNow: 500 });
    render(<Lore api={api} path={PATH} />);
    await fill();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(document.querySelector(".work-progress")?.getAttribute("data-state")).toBe("stalled");
  });

  it("shows a running clock for an upload that was just sent", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    api.loreAdd = async () => ({ id: "0".repeat(32), state: "waiting", stage: "waiting", error: "", source: "paste", choice: "smart", namesFound: 0, linksFound: 0, done: 0, total: 0, createdAt: 100, updatedAt: 100, serverNow: 110 });
    render(<Lore api={api} path={PATH} />);
    await fill();
    fireEvent.click(screen.getByLabelText(words.rights));
    fireEvent.click(screen.getByRole("button", { name: words.submit }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(document.querySelector(".work-progress")?.getAttribute("data-state")).toBe("running");
    expect(document.body.textContent).toMatch(/Running for/);
  });
});
