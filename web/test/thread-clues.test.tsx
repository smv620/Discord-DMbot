/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The clues of a story thread on the quest sheet (#4625): the count and each clue, adding one, marking and taking off, Undo, one change
// at a time, and nothing at all for a refused read. The panel is mounted by the quest sheet only while THREAD_CLUES_LIVE is on.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import QuestSheet from "../src/account/QuestSheet";
import ThreadClues from "../src/account/ThreadClues";
import { threadClueWords as w } from "../src/content/thread-clues-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/thread-clues-switch", () => ({
  get THREAD_CLUES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";
function open(api = mockApi("table")) {
  render(<ThreadClues api={api} refId={REF} thread="t1" />);
  return api;
}
const rowFor = (name: string) => screen.getByText(name).closest("li") as HTMLElement;

describe("the clues of a story thread", () => {
  it("shows the count and each clue found, not found and false lead", async () => {
    open();
    expect(await screen.findByText("Torn map")).toBeTruthy();
    expect(screen.getByText("1 of 3 clues found (1 is a false lead). Not yet found: 2.")).toBeTruthy();
    expect(rowFor("Torn map").textContent).toContain(w.found);
    expect(rowFor("Muddy boot print").textContent).toContain(w.notFound);
    expect(rowFor("Odd smell").textContent).toContain(w.falseLead);
  });

  it("adding a clue sends the name once and clears the field", async () => {
    const api = open();
    await screen.findByText("Torn map");
    const field = screen.getByLabelText(w.addLabel) as HTMLInputElement;
    fireEvent.input(field, { target: { value: "Silver key" } });
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    expect(await screen.findByText(w.added("Silver key"))).toBeTruthy();
    expect(api.calls.filter((c) => c === "changeThreadClues:100.cmp1:t1:add")).toHaveLength(1);
    await waitFor(() => expect((screen.getByLabelText(w.addLabel) as HTMLInputElement).value).toBe(""));
  });

  it("marking a false lead and taking one off send their changes", async () => {
    const api = open();
    await screen.findByText("Muddy boot print");
    fireEvent.click(within(rowFor("Muddy boot print")).getByRole("button", { name: w.falseLeadOn }));
    expect(await screen.findByText(w.falseLeadDone)).toBeTruthy();
    await waitFor(() => expect(within(rowFor("Muddy boot print")).getByRole("button", { name: w.falseLeadOff })).toBeTruthy());
    fireEvent.click(within(rowFor("Odd smell")).getByRole("button", { name: w.off }));
    expect(await screen.findByText(w.offDone)).toBeTruthy();
    expect(api.calls).toContain("changeThreadClues:100.cmp1:t1:word");
    expect(api.calls).toContain("changeThreadClues:100.cmp1:t1:off");
  });

  it("undo sends the batch and the list is read again", async () => {
    const api = open();
    await screen.findByText("Torn map");
    const real = api.changeThreadClues!.bind(api);
    const sent: unknown[] = [];
    api.changeThreadClues = async (ref, thread, change) => {
      sent.push(change);
      return real(ref, thread, change);
    };
    fireEvent.click(within(rowFor("Muddy boot print")).getByRole("button", { name: w.falseLeadOn }));
    await screen.findByText(w.falseLeadDone);
    await waitFor(() => expect(screen.getByRole("button", { name: w.undo })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: w.undo }));
    expect(await screen.findByText(w.undone)).toBeTruthy();
    expect(sent).toContainEqual({ action: "undo", batch: 201 });
    expect(api.calls.filter((c) => c === "threadClues:100.cmp1:t1").length).toBeGreaterThan(2);
  });

  it("all buttons wait while one change is saving", async () => {
    const api = open();
    await screen.findByText("Torn map");
    let release!: () => void;
    api.changeThreadClues = async () => {
      await new Promise<void>((r) => (release = r));
      return { batch: null };
    };
    fireEvent.click(within(rowFor("Torn map")).getByRole("button", { name: w.falseLeadOn }));
    expect(await screen.findByText(w.saving)).toBeTruthy();
    for (const b of screen.getAllByRole("button")) if (b.textContent !== undefined) expect((b as HTMLButtonElement).disabled || b.textContent === w.add).toBe(true);
    release();
    await screen.findByText(w.falseLeadDone);
  });

  it("a refused read shows nothing at all", async () => {
    const api = mockApi("table");
    api.threadClues = async () => {
      throw new ApiError("not-allowed");
    };
    const { container } = render(<ThreadClues api={api} refId={REF} thread="t1" />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("is not shown while the switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    api.threadCard = async () => {
      throw new ApiError("not-found");
    };
    render(<QuestSheet api={api} refId={REF} thread={{ id: "t1", name: "Bring the heir home" }} onSelect={() => {}} />);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("threadClues:"))).toBe(false));
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });
});

