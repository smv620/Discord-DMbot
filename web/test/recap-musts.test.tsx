/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The recap must-haves on the Recap style page (#3329): DMbot's own thirteen under five headings, edit in place, remove,
// add, save the whole list (followed to its end), back to the defaults (asked once), nothing shown while the switch is off.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import RecapStyleView from "../src/account/RecapStyle";
import { mustsPage as m, recapStylePage as words } from "../src/content/recap-style-words";
import { entries, held } from "../src/content/whats-new";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/recap-style-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-style-words")>();
  return {
    ...real,
    get RECAP_MUSTS_LIVE() {
      return live.on;
    },
  };
});

const PATH = "/account/recap-style/100.cmp1";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  live.on = true;
});

const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
};
const inputs = (): HTMLInputElement[] => (screen.getAllByLabelText(/^Line \d+$/) as HTMLInputElement[]);

describe("the must-haves list", () => {
  it("shows nothing while the switch is off", async () => {
    live.on = false;
    render(<RecapStyleView api={mockApi("table")} path={PATH} />);
    await screen.findByText(words.usingOwn(1));
    expect(screen.queryByText(m.heading)).toBeNull();
  });

  it("shows DMbot's thirteen under the five headings, with a note that they are DMbot's own", async () => {
    render(<RecapStyleView api={mockApi("table")} path={PATH} />);
    expect(await screen.findByText(m.heading)).toBeTruthy();
    expect(screen.getByText(m.intro)).toBeTruthy();
    expect(screen.getByText(m.usingDefaults)).toBeTruthy();
    expect(inputs()).toHaveLength(13);
    for (const heading of Object.values(m.groups)) expect(screen.getByText(heading)).toBeTruthy();
    // Nothing to put back while the defaults are in use, and nothing to save yet.
    expect(screen.queryByRole("button", { name: m.defaultsButton })).toBeNull();
    expect((screen.getByRole("button", { name: m.save }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("saves the whole list in order, follows it, and then shows it as the campaign's own", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(m.heading);
    fireEvent.input(inputs()[0]!, { target: { value: "Where we began." } }); // edit in place
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${m.remove(2)}`) })); // remove the second
    fireEvent.input(screen.getByLabelText(m.addLabel), { target: { value: "Who paid the ferryman." } });
    fireEvent.click(screen.getByRole("button", { name: m.addButton }));
    expect(screen.getByText(m.unsaved)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: m.save }));
    await waitFor(() => expect(api.calls).toContain("saveRecapMusts:100.cmp1:13")); // 13 - 1 + 1
    await settle();
    expect(await screen.findByText(m.usingOwn(13))).toBeTruthy();
    expect(inputs()[0]!.value).toBe("Where we began.");
    expect(inputs()[12]!.value).toBe("Who paid the ferryman.");
    // A list of one's own has the defaults button, and no DMbot headings.
    expect(screen.getByRole("button", { name: m.defaultsButton })).toBeTruthy();
    expect(screen.queryByText(m.groups.framing!)).toBeNull();
  });

  it("asks once before going back to the defaults, and Keep leaves the list alone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(m.heading);
    fireEvent.input(inputs()[0]!, { target: { value: "Mine." } });
    fireEvent.click(screen.getByRole("button", { name: m.save }));
    await settle();
    await screen.findByText(m.usingOwn(13));
    fireEvent.click(screen.getByRole("button", { name: m.defaultsButton }));
    expect(screen.getByText(m.defaultsAsk)).toBeTruthy();
    fireEvent.click(within(screen.getByRole("group", { name: m.defaultsAsk })).getByRole("button", { name: m.keep }));
    expect(screen.queryByText(m.defaultsAsk)).toBeNull();
    expect(api.calls.some((c) => c.startsWith("recapMustsDefaults"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: m.defaultsButton }));
    fireEvent.click(screen.getByRole("button", { name: m.defaultsYes }));
    await waitFor(() => expect(api.calls).toContain("recapMustsDefaults:100.cmp1"));
    await settle();
    expect(await screen.findByText(m.usingDefaults)).toBeTruthy();
    expect(inputs()).toHaveLength(13);
  });

  it("does not offer another line past the limit, and an empty edit is dropped on save", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(m.heading);
    fireEvent.input(inputs()[0]!, { target: { value: "   " } }); // emptied: it is not a line any more
    fireEvent.click(screen.getByRole("button", { name: m.save }));
    await waitFor(() => expect(api.calls).toContain("saveRecapMusts:100.cmp1:12"));
  });

  it("rests the controls while a change is on its way", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<RecapStyleView api={mockApi("table")} path={PATH} />);
    await screen.findByText(m.heading);
    fireEvent.input(inputs()[0]!, { target: { value: "Changed." } });
    fireEvent.click(screen.getByRole("button", { name: m.save }));
    await screen.findByText(m.waitingSave);
    expect(inputs()[0]!.disabled).toBe(true);
    expect((screen.getByRole("button", { name: m.save }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("the words", () => {
  it("hold the What's new entry back exactly while the switch is off", () => {
    expect(held.some((h) => h.title === "See and change what every recap covers")).toBe(true);
    expect(entries.some((e) => e.title === "See and change what every recap covers")).toBe(false);
  });
});
