/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The relationship web's views (#2405): the DM's Full View / Players View, revealing a name or a
// connection, theories drawn dotted, and the player's "Peek behind the DM screen" switch with its
// warning, its greyed-out state when the DM turned it off, and the fall-back when they do meanwhile.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
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
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    const before = notRevealed().length;
    const button = screen.getAllByRole("button", { name: /^Reveal .* to the players$/ })[0] as HTMLElement;
    fireEvent.click(button);
    await waitFor(() => expect(calls(api, "webReveal")).toHaveLength(1));
    expect(calls(api, "webReveal")[0]).toMatch(/:name:/);
    await waitFor(() => expect(notRevealed().length).toBeLessThan(before));
    await screen.findByText(w.revealed);
  });

  it("says why a name could not be revealed, and keeps the button for another try", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    api.webReveal = async () => {
      throw new ApiError("not-allowed");
    };
    const button = screen.getAllByRole("button", { name: /^Reveal .* to the players$/ })[0] as HTMLButtonElement;
    fireEvent.click(button);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(button.disabled).toBe(false);
  });

  it("greys a Reveal button while the bot is still working, so it can't be pressed twice", async () => {
    const api = await open();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    let finish: () => void = () => {};
    let asked = 0;
    api.webReveal = () => {
      asked += 1;
      return new Promise<void>((resolve) => {
        finish = resolve;
      });
    };
    const button = screen.getAllByRole("button", { name: /^Reveal .* to the players$/ })[0] as HTMLButtonElement;
    fireEvent.click(button);
    await waitFor(() => expect(button.disabled).toBe(true));
    fireEvent.click(button);
    expect(asked).toBe(1);
    finish();
    await waitFor(() => expect(button.disabled).toBe(false));
  });

  it("says why a connection could not be revealed", async () => {
    const api = await open();
    api.webReveal = async () => {
      throw new ApiError("not-found");
    };
    // Open each pair's connections until one that isn't revealed shows its Reveal button.
    let button: HTMLButtonElement | undefined;
    for (const see of screen.getAllByRole("button", { name: /^See the connections/ })) {
      fireEvent.click(see);
      button = screen.queryAllByRole("button", { name: /^Reveal connection \d+ to the players$/ })[0] as HTMLButtonElement | undefined;
      if (button) break;
    }
    expect(button).toBeTruthy();
    fireEvent.click(button!);
    expect(await screen.findByRole("alert")).toBeTruthy();
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

describe("Find a name in the players' view (#2989)", () => {
  const search = async (api: ReturnType<typeof mockApi>, word: string) => {
    fireEvent.input(screen.getByLabelText(w.centreSearch), { target: { value: word } });
    await waitFor(() => expect(calls(api, "webFind").length + calls(api, "allNames").length).toBeGreaterThan(0));
    await act(async () => {});
  };
  it("asks the graph's own door, never the whole list, once a DM is in Players View", async () => {
    const api = await open();
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(notRevealed()).toHaveLength(0));
    await search(api, "a");
    expect(calls(api, "webFind").pop()).toMatch(/:view=players$/);
    expect(calls(api, "allNames")).toHaveLength(0);
  });

  it("never offers a name the players don't know yet, though a DM in Full View is", async () => {
    const api = mockApi("table");
    const graph = await api.webGraph!("100.cmp1", { center: null, hops: 8 });
    const hidden = graph.nodes.find((n) => n.revealed === false)!.name;
    render(<Web api={api} path={at()} />);
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.input(screen.getByLabelText(w.centreSearch), { target: { value: hidden } });
    expect(await screen.findByRole("button", { name: hidden })).toBeTruthy(); // Full View: offered
    fireEvent.click(screen.getByLabelText(w.playersView));
    await waitFor(() => expect(notRevealed()).toHaveLength(0));
    fireEvent.input(screen.getByLabelText(w.centreSearch), { target: { value: `${hidden} ` } });
    expect(await screen.findByText(w.centreNone)).toBeTruthy();
    expect(screen.queryByRole("button", { name: hidden })).toBeNull();
  });

  it("offers a DM in Full View the whole list as before", async () => {
    const api = await open();
    await search(api, "a");
    expect(calls(api, "allNames").length).toBeGreaterThan(0);
    expect(calls(api, "webFind")).toHaveLength(0);
  });

  it("offers a player only what the players know, and nothing if the API cannot say", async () => {
    const api = await open(".web-player");
    await search(api, "a");
    expect(calls(api, "webFind").length).toBeGreaterThan(0);
    cleanup();
    const old = mockApi("table");
    delete (old as { webFind?: unknown }).webFind;
    render(<Web api={old} path={at(".web-player")} />);
    await screen.findByText(/names?, \d+ lines?/);
    fireEvent.input(screen.getByLabelText(w.centreSearch), { target: { value: "a" } });
    await act(async () => {});
    expect(calls(old, "allNames")).toHaveLength(0);
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

  it("a failed peek that isn't the DM's switch leaves the switch usable and says why", async () => {
    const api = mockApi("table");
    api.webPeek = async () => {
      throw new ApiError("network");
    };
    await open(player, api);
    fireEvent.click(sw());
    fireEvent.click(await screen.findByRole("button", { name: w.peekYes }));
    await screen.findByText(w.errors.network!);
    expect(screen.queryByText(w.errors["peek-off"]!)).toBeNull(); // not "turned off by the DM"
    expect(sw().disabled).toBe(false);
    expect(screen.queryByText(w.peekOffNote)).toBeNull();
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
