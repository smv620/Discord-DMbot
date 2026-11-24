/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Things and places the story keeps coming back to but never names (#5175, the page half of #5173 slice 6): the name card asks
// 🔖 Keep it / Give it a name… / Not worth keeping for them only, and a kept one says it has no name of its own. Behind
// SINGLED_OUT_LIVE (off), so these tests switch it on; a name card with another `how` keeps its usual words.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi, mockRecaps } from "../src/account/mock";
import Names from "../src/account/Names";
import RecapNameChecks from "../src/account/RecapNameChecks";
import { nameFoundWords as fw } from "../src/content/name-found-words";
import { nameCheckWords as nw } from "../src/content/recap-name-checks";
import { namesPage as nameWords } from "../src/content/names-words";

const live = vi.hoisted(() => ({ singled: true }));
vi.mock("../src/content/name-found-switch", () => ({ NAME_FOUND_LIVE: true }));
vi.mock("../src/content/singled-out-switch", () => ({
  get SINGLED_OUT_LIVE() {
    return live.singled;
  },
}));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_NAME_CHECKS_LIVE() {
      return true;
    },
    get RECAPS_LIVE() {
      return true;
    },
    get NAME_CHANGE_WORDS_LIVE() {
      return true;
    },
  };
});

afterEach(() => {
  cleanup();
  live.singled = true;
});

const draft = mockRecaps()[0]!;
const row = (name: string) => screen.getByRole("group", { name }) as HTMLElement;
const lineText = "🔖 Not a name, but the story keeps coming back to it: “the old gemstone mine” (heard 4 times).";

/** The mock's Garrick, asked as a singled-out thing. */
function singledApi(how: "singled_out" | "introduced" = "singled_out") {
  const api = mockApi("table");
  const real = api.nameChecks!.bind(api);
  api.nameChecks = async (...a) => {
    const got = await real(...a);
    return {
      ...got,
      names: got.names.map((n) => (n.found ? { ...n, name: "The gemstone mine", found: { ...n.found, how, heard: lineText, guess: "place" } } : n)),
    };
  };
  return api;
}

describe("a singled-out thing on the name card", () => {
  it("shows the 🔖 line and the three answers for it", async () => {
    render(<RecapNameChecks api={singledApi()} refId={draft.ref} />);
    await screen.findByRole("heading", { name: nw.heading });
    const card = within(row("The gemstone mine"));
    expect(card.getByText(lineText)).toBeTruthy();
    expect(card.getByRole("button", { name: fw.singled.keepItFor("The gemstone mine") }).textContent).toBe(fw.singled.keepIt);
    expect(card.getByRole("button", { name: fw.singled.notWorthFor("The gemstone mine") }).textContent).toBe(fw.singled.notWorth);
    expect(card.getByText(fw.singled.giveName)).toBeTruthy();
    expect(card.queryByText(nw.notAName)).toBeNull();
  });

  it("Keep it confirms the guessed kind", async () => {
    const api = singledApi();
    const spy = vi.spyOn(api, "answerNameCheck");
    render(<RecapNameChecks api={api} refId={draft.ref} />);
    await screen.findByRole("heading", { name: nw.heading });
    fireEvent.click(within(row("The gemstone mine")).getByRole("button", { name: fw.singled.keepItFor("The gemstone mine") }));
    expect(spy).toHaveBeenCalledWith(draft.ref, "c".repeat(32), { action: "confirm", kind: "place" });
  });

  it("Not worth keeping says not a name, as the old button does", async () => {
    const api = singledApi();
    const spy = vi.spyOn(api, "answerNameCheck");
    render(<RecapNameChecks api={api} refId={draft.ref} />);
    await screen.findByRole("heading", { name: nw.heading });
    fireEvent.click(within(row("The gemstone mine")).getByRole("button", { name: fw.singled.notWorthFor("The gemstone mine") }));
    expect(spy).toHaveBeenCalledWith(draft.ref, "c".repeat(32), { action: "not_a_name" });
  });

  it("another kind of found name keeps its usual words", async () => {
    render(<RecapNameChecks api={singledApi("introduced")} refId={draft.ref} />);
    await screen.findByRole("heading", { name: nw.heading });
    const card = within(row("The gemstone mine"));
    expect(card.queryByText(fw.singled.keepIt)).toBeNull();
    expect(card.getByText(nw.notAName)).toBeTruthy();
  });

  it("keeps the usual words while its switch is off", async () => {
    live.singled = false;
    render(<RecapNameChecks api={singledApi()} refId={draft.ref} />);
    await screen.findByRole("heading", { name: nw.heading });
    expect(within(row("The gemstone mine")).queryByText(fw.singled.keepIt)).toBeNull();
  });

  it("the shipped switch is off", async () => {
    const real = await vi.importActual<typeof import("../src/content/singled-out-switch")>("../src/content/singled-out-switch");
    expect(real.SINGLED_OUT_LIVE).toBe(false);
  });
});

describe("a kept one on the Names page", () => {
  it("says it is described in the story, with no name of its own", async () => {
    const api = mockApi("table");
    const real = api.allNames!.bind(api);
    api.allNames = async (...a) => {
      const got = await real(...a);
      return { ...got, names: got.names.map((n, i) => (i === 0 ? { ...n, described: true } : n)) };
    };
    render(<Names api={api} path="/account/names/100.cmp1" />);
    await screen.findByRole("list", { name: nameWords.listLabel });
    expect(await screen.findAllByText(fw.singled.described)).toHaveLength(1);
  });
});
