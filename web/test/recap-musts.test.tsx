/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The recap must-haves on the Recap style page (#3329): DMbot's own thirteen under five headings, edit in place, remove,
// add, save the whole list (followed to its end), back to the defaults (asked once), nothing shown while the switch is off.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { httpApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import RecapStyleView from "../src/account/RecapStyle";
import { mustsPage as m, RECAP_STYLE_LIVE, recapStylePage as words } from "../src/content/recap-style-words";
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

  it("drops an emptied line on save", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(m.heading);
    fireEvent.input(inputs()[0]!, { target: { value: "   " } }); // emptied: it is not a line any more
    fireEvent.click(screen.getByRole("button", { name: m.save }));
    await waitFor(() => expect(api.calls).toContain("saveRecapMusts:100.cmp1:12"));
  });

  it("keeps unsaved edits when something else on the page (a note) is saved and the style is read again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<RecapStyleView api={mockApi("table")} path={PATH} />);
    await screen.findByText(m.heading);
    fireEvent.input(inputs()[0]!, { target: { value: "Still mine." } });
    fireEvent.input(screen.getByLabelText(words.noteLabel), { target: { value: "Keep it short." } });
    fireEvent.click(screen.getByRole("button", { name: words.addNote }));
    await settle();
    await waitFor(() => expect(document.body.textContent).toContain("Keep it short."));
    expect(inputs()[0]!.value).toBe("Still mine."); // the reload brought the same list, so the draft stayed
  });

  it("sends a line that was typed but not added, and will not save an empty list", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi("table");
    render(<RecapStyleView api={api} path={PATH} />);
    await screen.findByText(m.heading);
    fireEvent.input(screen.getByLabelText(m.addLabel), { target: { value: "Typed, not added." } });
    fireEvent.click(screen.getByRole("button", { name: m.save }));
    await waitFor(() => expect(api.calls).toContain("saveRecapMusts:100.cmp1:14")); // 13 + the typed line
    await settle();
    // Remove every line: nothing to save, and the words say how to start over.
    for (let i = 14; i >= 1; i--) fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${m.remove(i)}`) }));
    expect(screen.getByText(m.emptyList)).toBeTruthy();
    expect((screen.getByRole("button", { name: m.save }) as HTMLButtonElement).disabled).toBe(true);
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
  it("hold the What's new entry back until the Recap style page itself is linked", () => {
    expect(held.some((h) => h.title === "See and change what every recap covers")).toBe(!RECAP_STYLE_LIVE);
    expect(entries.some((e) => e.title === "See and change what every recap covers")).toBe(RECAP_STYLE_LIVE);
  });
});

describe("the routes as CloudDev-lead built them (#3340)", () => {
  const over = (status: number, body: unknown, seen: { method: string; url: string; body: string }[] = []) =>
    httpApi("https://api.example.test", (async (url: string, init: RequestInit) => {
      seen.push({ method: String(init.method), url: String(url), body: String(init.body ?? "") });
      return new Response(JSON.stringify(body), { status });
    }) as unknown as typeof fetch);

  it("saves with PUT and the lines, and goes back to the defaults with DELETE", async () => {
    const seen: { method: string; url: string; body: string }[] = [];
    const api = over(200, { id: "a".repeat(32) }, seen);
    await api.saveRecapMusts!("100.cmp1", ["One.", "Two."]);
    await api.recapMustsDefaults!("100.cmp1");
    expect(seen[0]).toMatchObject({ method: "PUT", body: JSON.stringify({ lines: ["One.", "Two."] }) });
    expect(seen[0]!.url).toContain("/me/campaigns/100.cmp1/recap-style/must-haves");
    expect(seen[1]).toMatchObject({ method: "DELETE" });
  });

  it("reads its refusals: too many, empty, too long and not allowed", async () => {
    const ask = (detail: string, status = 409) => over(status, { detail }).saveRecapMusts!("100.cmp1", ["x"]);
    await expect(ask("too_many")).rejects.toMatchObject({ kind: "musts-full" });
    await expect(ask("empty")).rejects.toMatchObject({ kind: "musts-empty" });
    await expect(ask("too_long")).rejects.toMatchObject({ kind: "musts-too-long" });
    await expect(ask("not_allowed", 403)).rejects.toMatchObject({ kind: "not-allowed" });
    expect(words.problems["musts-full"]).toContain("40 lines");
    expect(words.problems["musts-too-long"]).toBe("Nothing was changed. A line can be up to 300 characters. Shorten it and try again.");
    expect(words.examplesIntro).toBe("These examples shape how your recaps sound. The list above decides what goes in them.");
  });
});
