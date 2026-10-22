/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The relationship web's views (#2405): the DM's Full View / Players View, revealing a name or a
// connection, theories drawn dotted, and the player's "Peek behind the DM screen" switch with its
// warning, its greyed-out state when the DM turned it off, and the fall-back when they do meanwhile.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

afterEach(cleanup);

const at = (suffix = ""): string => `/account/web/100.cmp1${suffix}`;
const calls = (api: ReturnType<typeof mockApi>, prefix: string) => api.calls.filter((c) => c.startsWith(prefix));
async function open(suffix = "", api = mockApi("table")) {
  render(<Web api={api} path={at(suffix)} />);
  await screen.findByText(/names?, \d+ lines?/);
  return api;
}
const notRevealed = () => screen.queryAllByText(`(${w.notRevealed})`, { exact: false });
const lastGraph = (api: ReturnType<typeof mockApi>) => calls(api, "webGraph").pop() ?? "";

describe("a DM's Full View and Players View", () => {
  it("starts in Full View: secrets, and what isn't revealed yet shown dimmed and said", async () => {
    await open();
    expect((screen.getByLabelText(w.fullView) as HTMLInputElement).checked).toBe(true);
    expect(notRevealed().length).toBeGreaterThan(0);
    expect(screen.getByText(w.legendNotRevealed)).toBeTruthy();
    expect(screen.getAllByText(w.reveal, { exact: false }).length).toBeGreaterThan(0);
  });

  it("asks for each view, and the Players View is what a player sees, read-only", async () => {
    const api = await open();
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(lastGraph(api)).toContain("view=players"));
    await waitFor(() => expect(notRevealed()).toHaveLength(0));
    expect(screen.queryByText(w.legendNotRevealed)).toBeNull();
    expect(screen.queryAllByRole("button", { name: /^Change / })).toHaveLength(0);
    expect(screen.queryAllByText(w.reveal, { exact: false })).toHaveLength(0);
    expect(screen.getByText(w.playersViewNote)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(w.fullView));
    await waitFor(() => expect(lastGraph(api)).toContain("view=full"));
    await waitFor(() => expect(notRevealed().length).toBeGreaterThan(0));
  });

  it("stays a DM's page in the Players View (the switch is still there to go back)", async () => {
    await open();
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(notRevealed()).toHaveLength(0));
    expect(screen.getByLabelText(w.fullView)).toBeTruthy();
  });

  it("reveals a name to the players, then reads the web again", async () => {
    const api = await open();
    const before = notRevealed().length;
    const button = screen.getAllByRole("button", { name: /^Reveal .* to the players$/ })[0] as HTMLElement;
    fireEvent.click(button);
    await waitFor(() => expect(calls(api, "webReveal")).toHaveLength(1));
    expect(calls(api, "webReveal")[0]).toMatch(/:name:/);
    await waitFor(() => expect(notRevealed().length).toBeLessThan(before));
    await screen.findByText(w.revealed);
  });
});

describe("what a player must never see", () => {
  it("keeps secret and unrevealed things off the screen even if the API sends them", async () => {
    const api = mockApi("table");
    const full = api.webGraph!.bind(api);
    // An API that over-sends: the DM's full web, with only canEdit turned off.
    api.webGraph = async (ref, q) => ({ ...(await full(ref.replace(".web-player", ""), { ...q, view: "full" })), canEdit: false });
    await open(".web-player", api);
    expect(notRevealed()).toHaveLength(0);
    expect(screen.queryAllByText(w.secretMark, { exact: false })).toHaveLength(0);
    expect(screen.queryByText(w.legendNotRevealed)).toBeNull();
  });

  it("drops what the players don't know the moment a DM switches to the Players View", async () => {
    const api = mockApi("table");
    await open("", api);
    expect(notRevealed().length).toBeGreaterThan(0);
    const slow = api.webGraph!.bind(api);
    api.webGraph = (ref, q) => new Promise((go) => setTimeout(() => go(slow(ref, q)), 50));
    fireEvent.click(screen.getByLabelText(w.playersView));
    expect(notRevealed()).toHaveLength(0); // before the new web arrives
  });
});

describe("theories", () => {
  async function openTheory(suffix = "") {
    const api = await open(suffix);
    // The list's lines carry a ? for a theory.
    const theory = screen.getAllByRole("listitem").find((li) => li.textContent?.startsWith(w.theoryMark));
    return { api, theory };
  }

  it("marks a line the party only suspects, and its panel says what it is and how sure", async () => {
    const { theory } = await openTheory();
    expect(theory).toBeTruthy();
    fireEvent.click(within(theory as HTMLElement).getByRole("button", { name: /^See the connections/ }));
    expect(await screen.findByText(/Party theory/)).toBeTruthy();
    expect(screen.getByText(/60% sure/)).toBeTruthy();
  });

  it("marks, in Full View, what the party believes and the truth holds false", async () => {
    await open();
    // The accusation is the one the party is wrong about: open each theory's panel until it shows.
    const buttons = screen
      .getAllByRole("listitem")
      .filter((x) => x.textContent?.startsWith(w.theoryMark))
      .map((x) => within(x).getByRole("button", { name: /^See the connections/ }));
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[0] as HTMLElement);
    expect(await screen.findByText(w.believesFalse)).toBeTruthy();
  });

  it("does not tell a player the party is wrong", async () => {
    const api = mockApi("table");
    render(<Web api={api} path={at(".web-player")} />);
    await screen.findByText(/names?, \d+ lines?/);
    const buttons = screen
      .getAllByRole("listitem")
      .filter((x) => x.textContent?.startsWith(w.theoryMark))
      .map((x) => within(x).getByRole("button", { name: /^See the connections/ }));
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[0] as HTMLElement);
    expect(await screen.findByText(/Party theory/)).toBeTruthy(); // the theory itself is shown
    expect(screen.queryByText(w.believesFalse)).toBeNull(); // not that it is wrong
  });
});

describe("a player's peek", () => {
  const player = ".web-player";
  const sw = () => screen.getByRole("switch", { name: w.peekSwitch }) as HTMLInputElement;

  it("starts in the normal view: nothing secret or unrevealed, no DM controls", async () => {
    const api = await open(player);
    expect(sw().checked).toBe(false);
    expect(screen.getByText(w.normalView)).toBeTruthy();
    expect(notRevealed()).toHaveLength(0);
    expect(screen.queryByLabelText(w.fullView)).toBeNull();
    expect(calls(api, "webGraph").every((c) => !c.includes("view="))).toBe(true);
  });

  it("warns first, and Cancel changes nothing", async () => {
    const api = await open(player);
    fireEvent.click(sw());
    const dialog = await screen.findByRole("alertdialog", { name: w.peekTitle });
    expect(within(dialog).getByText(w.peekWarn)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: w.peekCancel }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(calls(api, "webPeek")).toHaveLength(0);
    expect(calls(api, "webGraph").every((c) => !c.includes("view="))).toBe(true);
    expect(sw().checked).toBe(false);
    expect(document.activeElement).toBe(sw());
  });

  it("on Yes tells the API, then reads the full web, read-only; off goes back", async () => {
    const api = await open(player);
    fireEvent.click(sw());
    fireEvent.click(await screen.findByRole("button", { name: w.peekYes }));
    await waitFor(() => expect(calls(api, "webPeek")).toHaveLength(1));
    await waitFor(() => expect(lastGraph(api)).toContain("view=full"));
    expect(api.calls.indexOf(calls(api, "webPeek")[0]!)).toBeLessThan(api.calls.indexOf(lastGraph(api)));
    await waitFor(() => expect(notRevealed().length).toBeGreaterThan(0));
    expect(screen.getByText(w.peeking, { exact: false })).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /^Change / })).toHaveLength(0);
    expect(screen.queryAllByText(w.reveal, { exact: false })).toHaveLength(0);
    expect(sw().checked).toBe(true);
    fireEvent.click(sw());
    await waitFor(() => expect(notRevealed()).toHaveLength(0));
    expect(sw().checked).toBe(false);
  });

  it("is greyed out with the reason when the DM turned peeking off, and cannot be turned on", async () => {
    const api = await open(".web-private");
    const sw1 = sw();
    expect(sw1.getAttribute("aria-disabled")).toBe("true");
    const note = screen.getByText(w.peekOffNote);
    expect(sw1.getAttribute("aria-describedby")).toBe(note.id);
    fireEvent.click(sw1);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(calls(api, "webPeek")).toHaveLength(0);
  });

  it("falls back to the normal view, with the reason, when the DM turns peeking off meanwhile", async () => {
    const api = mockApi("table");
    const read = api.webGraph!.bind(api);
    api.webGraph = async (ref, q) => {
      if (q.view === "full") throw new ApiError("peek-off");
      return read(ref, q);
    };
    await open(player, api);
    fireEvent.click(sw());
    fireEvent.click(await screen.findByRole("button", { name: w.peekYes }));
    await screen.findByText(w.errors["peek-off"]!);
    await waitFor(() => expect(sw().checked).toBe(false));
    expect(notRevealed()).toHaveLength(0);
  });

  it("falls back too when the peek itself is refused", async () => {
    const api = mockApi("table");
    api.webPeek = async () => {
      throw new ApiError("peek-off");
    };
    await open(player, api);
    fireEvent.click(sw());
    fireEvent.click(await screen.findByRole("button", { name: w.peekYes }));
    await screen.findByText(w.errors["peek-off"]!);
    expect(sw().checked).toBe(false);
  });
});
