/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph's clean-up (#3740, behind GRAPH_CLEANUP_LIVE): the groups no chain links to the party, each with its
// scene, Add a connection and (for an unnamed person) the Names page's own panel; the empty, no-player-character, cut-off,
// failed and switched-off states; and nothing for anyone who can't edit.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { cleanupWords as w } from "../src/content/graph-cleanup-words";
import { namePlaceWords } from "../src/content/name-places";
import { webPage } from "../src/content/web-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/graph-cleanup-switch", () => ({
  get GRAPH_CLEANUP_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

async function open(kind = "cleanup", api = mockApi("table")) {
  render(<Web api={api} path={`/account/web/100.cmp1.${kind}`} />);
  await screen.findByText(/Showing \d+ names? and \d+ connections?/);
  fireEvent.click(screen.getByRole("button", { name: webPage.cleanupOpen }));
  return api;
}

describe("the clean-up", () => {
  it("lists the groups largest first with their scenes, and says what has none", async () => {
    await open();
    await screen.findByText(w.count(6));
    expect(screen.getAllByRole("heading", { level: 3, name: w.group })).toHaveLength(3);
    expect(screen.getByText(/Okthandom led the party over the pass at dusk\./)).toBeTruthy();
    expect(screen.getAllByText(w.noScene).length).toBe(3);
    expect(screen.getByText("unnamed - Okthandom's child")).toBeTruthy();
    expect(screen.getAllByText(`(${w.waiting})`).length).toBeGreaterThan(0);
  });

  it("says nothing is waiting when no group is isolated", async () => {
    await open("cleanup-empty");
    expect((await screen.findByText(w.nothing)).textContent).toBe(w.nothing);
  });

  it("says a campaign with no player character has to add one first", async () => {
    await open("cleanup-nopc");
    expect(await screen.findByText(w.noPlayerCharacter)).toBeTruthy();
  });

  it("says when the list is cut off", async () => {
    await open("cleanup-big");
    expect(await screen.findByText(w.showing(300, 450))).toBeTruthy();
  });

  it("shows the review line in counts", async () => {
    await open("cleanup");
    expect(await screen.findByText(w.reviewWaiting(12, 2))).toBeTruthy();
    cleanup();
    await open("cleanup-done");
    expect(await screen.findByText(w.reviewDone(2))).toBeTruthy();
    cleanup();
    await open("cleanup-off");
    await screen.findByText(w.count(6));
    expect(screen.queryByText(/DMbot will check/)).toBeNull();
  });

  it("adds a connection: find who, say how, send it, and read the list again", async () => {
    const api = mockApi("table");
    // (the pretend map doesn't hold the clean-up's pretend people, so the call itself is stubbed to succeed)
    const sent: string[] = [];
    api.changeWebLink = async (_ref, change) => {
      sent.push(JSON.stringify(change));
      return { removed: true };
    };
    await open("cleanup", api);
    await screen.findByText(w.count(6));
    fireEvent.click(screen.getByRole("button", { name: w.addHeading("Old Pell") }));
    fireEvent.input(screen.getByLabelText(w.findLabel), { target: { value: "Mira" } });
    fireEvent.click(await screen.findByRole("button", { name: /^Connect to Mira/ }));
    fireEvent.input(screen.getByLabelText(w.wordsLabel), { target: { value: "sells rope to" } });
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    await waitFor(() => expect(sent.some((c) => c.includes("sells rope to") && c.includes('"a":"e4"'))).toBe(true));
    expect((await screen.findAllByText(w.added)).length).toBeGreaterThan(0);
    expect(api.calls.filter((c) => c.startsWith("graphCleanup")).length).toBeGreaterThan(1);
  });

  it("says neutral words, not an all-clear, when the door doesn't say whether there is a player character", async () => {
    const api = mockApi("table");
    const real = api.graphCleanup!.bind(api);
    api.graphCleanup = async (ref) => {
      const { noPlayerCharacter: _gone, ...rest } = await real(ref);
      return { ...rest, groups: [], total: 0 };
    };
    await open("cleanup", api);
    expect(await screen.findByText(w.nothingListed)).toBeTruthy();
  });

  it("clears the search when the text is cleared", async () => {
    await open();
    await screen.findByText(w.count(6));
    fireEvent.click(screen.getByRole("button", { name: w.addHeading("Old Pell") }));
    const box = screen.getByLabelText(w.findLabel);
    fireEvent.input(box, { target: { value: "Mi" } });
    fireEvent.input(box, { target: { value: "" } });
    await waitFor(() => expect(screen.getByText(w.findHint)).toBeTruthy());
    expect(screen.queryByRole("button", { name: /^Connect to / })).toBeNull();
  });

  it("offers Where was it said? only for an entry with no scene, and shows the lines it is given", async () => {
    const api = mockApi("table");
    const asked: string[] = [];
    api.cleanupPlaces = async (_ref, id) => {
      asked.push(id);
      return { places: [{ session: 2, available: true, lines: [{ speaker: "Mira", at: 1_800_000_000, text: "Brewner paid for the rope." }] }], morePlaces: 0 };
    };
    await open("cleanup", api);
    await screen.findByText(w.count(6));
    expect(screen.getAllByRole("button", { name: new RegExp(`^${namePlaceWords.button.replace("?", "\\?")}: `) })).toHaveLength(3); // (e3, e5 and e2 have no scene)
    expect(asked).toEqual([]); // (nothing is read until a button is pressed)
    fireEvent.click(screen.getByRole("button", { name: `${namePlaceWords.button}: Brewner` }));
    expect(await screen.findByText(/Brewner paid for the rope\./)).toBeTruthy();
    expect(asked).toEqual(["e5"]);
  });

  it("reads the lines from the clean-up's own places call, with the pretend API's answer", async () => {
    const api = await open();
    await screen.findByText(w.count(6));
    fireEvent.click(screen.getByRole("button", { name: `${namePlaceWords.button}: Brewner` }));
    expect(await screen.findByText(/They talked about it by the fire\./)).toBeTruthy();
    expect(api.calls).toContain("cleanupPlaces:100.cmp1.cleanup:e5");
    expect(api.calls.some((c) => c.startsWith("clarifyName"))).toBe(false);
  });

  it("says plainly when there are no lines for an entry", async () => {
    const api = mockApi("table");
    api.cleanupPlaces = async () => {
      throw new ApiError("not-found");
    };
    await open("cleanup", api);
    await screen.findByText(w.count(6));
    fireEvent.click(screen.getByRole("button", { name: `${namePlaceWords.button}: Brewner` }));
    expect(await screen.findByText(w.noLines)).toBeTruthy();
  });

  it("links a named entry to the Names page, and an unnamed one has the panel instead", async () => {
    await open();
    await screen.findByText(w.count(6));
    const link = screen.getByRole("link", { name: `${w.namesPage}: Old Pell` });
    // (the name is in the fragment, which the Names page reads to open that name's editor; a fragment is never sent to a server)
    expect(link.getAttribute("href")).toBe("/account/names/100.cmp1.cleanup#said=Old%20Pell");
    expect(screen.queryByRole("link", { name: `${w.namesPage}: unnamed - Okthandom's child` })).toBeNull();
  });

  it("asks for both a person and words before adding", async () => {
    await open();
    await screen.findByText(w.count(6));
    fireEvent.click(screen.getByRole("button", { name: w.addHeading("Old Pell") }));
    fireEvent.click(screen.getByRole("button", { name: w.add }));
    expect(await screen.findByText(w.needBoth)).toBeTruthy();
  });

  it("offers another try when the read fails", async () => {
    const api = mockApi("table");
    const real = api.graphCleanup!.bind(api);
    let first = true;
    api.graphCleanup = async (ref) => {
      if (first) {
        first = false;
        throw new ApiError("server");
      }
      return real(ref);
    };
    await open("cleanup", api);
    await screen.findByText(w.down);
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    await screen.findByText(w.count(6));
  });

  it("offers the Names page's panel for an unnamed person only", async () => {
    await open();
    await screen.findByText(w.count(6));
    expect(screen.getAllByRole("button", { name: new RegExp(`^${w.nameThem}: `) })).toHaveLength(2);
  });

  it("is not offered while the switch is off", async () => {
    live.on = false;
    render(<Web api={mockApi("table")} path="/account/web/100.cmp1.cleanup" />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    expect(screen.queryByRole("button", { name: webPage.cleanupOpen })).toBeNull();
  });

  it("is not offered to someone who can't edit", async () => {
    const api = mockApi("table");
    const real = api.webGraph!.bind(api);
    api.webGraph = async (ref, q) => ({ ...(await real(ref, q)), canEdit: false });
    render(<Web api={api} path="/account/web/100.cmp1.cleanup" />);
    await screen.findByText(/Showing \d+ names? and \d+ connections?/);
    expect(screen.queryByRole("button", { name: webPage.cleanupOpen })).toBeNull();
  });
});
